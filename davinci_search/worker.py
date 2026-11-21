"""Own the queue across Resolve sessions and run ASR and Codex outside Resolve."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Thread
import time
from typing import Callable, Generator
from uuid import uuid4

from .diagnostics import DiagnosticLog, ToolFailure
from .index import Index, Media, RunStatus, Transcript
from .polish import Codex, CodexUnavailable
from .process_tree import start_queue_process
from .runtime import Settings, python_module_command
from .scratch import cleanup_scratch, run_scratch, scratch_path
from .transcribe import Whisper

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def claim_worker(path: Path) -> Generator[bool, None, None]:
    # The OS releases the byte-range lock if the worker or Resolve crashes.
    with path.with_name("worker.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
        else:
            try:
                yield True
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def worker_active(index: Index) -> bool:
    with claim_worker(index.path) as acquired:
        return not acquired


STARTUP_SECONDS = 15
STOPPABLE = ("starting", "running")


def starting(run: RunStatus | None) -> bool:
    return bool(run and run.state in ("starting", "stopping")
                and time.time() - run.started_at < STARTUP_SECONDS)


def recover_scratch(index: Index) -> bool:
    with claim_worker(index.path) as acquired:
        return not acquired or cleanup_scratch(index.path)


def start_recovery(index: Index, settings: Settings) -> subprocess.Popen[bytes]:
    root = Path(__file__).resolve().parent.parent
    return subprocess.Popen(
        python_module_command(settings.python, "davinci_search", "cleanup", "--db", str(index.path)),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def request_stop(index: Index, token: str) -> bool:
    return index.change_run(token, STOPPABLE, "stopping")


def stop_requested(index: Index, token: str) -> bool:
    run = index.run_status()
    return bool(run and run.token == token and run.state == "stopping")


def worker_command(settings: Settings, index: Index, command: str, token: str) -> list[str]:
    return python_module_command(settings.python, "davinci_search", command,
                                 "--config", str(settings.config_file), "--db", str(index.path), "--run", token)


def request_index(index: Index, settings: Settings, scan: Callable[[], list[Media]]) -> bool:
    with claim_worker(index.path) as acquired:
        if not acquired or starting(index.run_status()):
            return False
        token = uuid4().hex
        index.queue(scan(), token)
    diagnostics = DiagnosticLog(settings.config_file.parent, index.path.parent.name, token)
    diagnostics.record("index_requested")
    root = Path(__file__).resolve().parent.parent
    log_file = index.path.with_name("worker.log")
    try:
        with log_file.open("ab") as log:
            process = subprocess.Popen(
                worker_command(settings, index, "worker", token),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=root,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        Thread(target=process.wait, daemon=True).start()
    except OSError as error:
        diagnostics.failure("worker_error", "worker_launch", error)
        if not index.change_run(token, ("starting",), "failed"):
            index.change_run(token, ("stopping",), "stopped")
        raise
    return True


def run_queue(
    index: Index, transcribe: Callable[[Media], Transcript],
    polish: Callable[[dict[int, str]], dict[int, str]],
    diagnostics: DiagnosticLog | None = None,
) -> None:
    polish_unavailable: str | None = None

    def check_revision(media: Media) -> None:
        if Media.from_path(media.key, media.name, media.path).revision != media.revision:
            raise ToolFailure("Media thay đổi hoặc mất kết nối. Hãy quét lại Media Pool.", "media", "changed")

    def record_failure(media: Media, step: str, error: Exception) -> None:
        if diagnostics is not None:
            diagnostics.failure("media_error", step, error)
        index.record_error(media, str(error))

    def finish_polish(media: Media) -> None:
        nonlocal polish_unavailable
        try:
            check_revision(media)
            if polish_unavailable:
                index.record_error(media, polish_unavailable)
                return
            edits = polish(index.raw(media))
            check_revision(media)
            index.save_polish(media, edits)
        except CodexUnavailable as error:
            polish_unavailable = str(error)
            record_failure(media, "codex", error)
        except (OSError, ValueError, RuntimeError) as error:
            record_failure(media, "codex", error)

    for media in index.pending("polish"):
        finish_polish(media)
    for media in index.pending("asr"):
        try:
            check_revision(media)
            transcript = transcribe(media)
            check_revision(media)
            index.save_transcript(media, transcript)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
            record_failure(media, "transcription", error)
            continue
        if transcript.segments:
            finish_polish(media)
    index.finish()


def run_worker(config_file: Path, database: Path, token: str) -> None:
    diagnostics = DiagnosticLog(config_file.parent, database.parent.name, token)
    try:
        settings = Settings.load(config_file)
        index = Index(database)
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            with claim_worker(index.path) as acquired:
                if acquired:
                    supervise(index, settings, token, diagnostics)
                    return
            time.sleep(0.1)
        index.change_run(token, ("starting",), "failed")
        diagnostics.record("worker_error", step="worker_startup", reason="lock_timeout")
    except Exception as error:
        diagnostics.failure("worker_error", "worker", error)
        raise


def supervise(index: Index, settings: Settings, token: str, diagnostics: DiagnosticLog) -> None:
    if not index.change_run(token, ("starting",), "running"):
        if index.change_run(token, ("stopping",), "stopped"):
            diagnostics.record("index_ended", reason="stopped")
        return
    diagnostics.record("index_started")
    outcome = "failed"
    exit_code = None
    try:
        cleanup_scratch(index.path)
        if stop_requested(index, token):
            return
        run_scratch(index.path, token)
        with start_queue_process(worker_command(settings, index, "queue", token)) as process:
            while process.poll() is None:
                if stop_requested(index, token):
                    break
                time.sleep(0.15)
            exit_code = process.returncode
            if exit_code == 0:
                outcome = "finished"
    finally:
        # Windows can release file handles shortly after the job reports zero processes.
        deadline = time.monotonic() + 2
        while cleanup_scratch(index.path, token):
            if time.monotonic() >= deadline:
                print("Còn file tạm chờ dọn khi được nhả khóa.", flush=True)
                break
            time.sleep(0.1)
        index.end_recognition(token)
        if not index.change_run(token, ("running",), outcome):
            if index.change_run(token, ("stopping",), "stopped"):
                outcome = "stopped"
        diagnostics.record("index_ended", reason=outcome, code=exit_code)


def run_queue_process(config_file: Path, database: Path, token: str) -> None:
    gate = sys.stdin.buffer
    if gate.read(1) != b"1":
        return
    if os.name != "nt":
        def watch_supervisor() -> None:
            os.read(gate.fileno(), 1)
            os.killpg(os.getpgrp(), signal.SIGKILL)

        Thread(target=watch_supervisor, daemon=True).start()
    diagnostics = DiagnosticLog(config_file.parent, database.parent.name, token)
    try:
        settings = Settings.load(config_file)
        index = Index(database)
        scratch = scratch_path(database) / f"run-{token}"
        last_backend = ""

        def report_backend(backend: str, device: str, reason: str) -> None:
            nonlocal last_backend
            index.report_backend(token, backend, device, reason)
            if backend and backend != last_backend:
                last_backend = backend
                diagnostics.record("transcription_backend", backend=backend)

        whisper = Whisper(settings, scratch, report_backend, lambda: index.end_recognition(token), diagnostics)
        run_queue(index, whisper.transcribe, Codex(settings.codex, scratch).polish, diagnostics)
    except Exception as error:
        diagnostics.failure("worker_error", "queue", error)
        raise
