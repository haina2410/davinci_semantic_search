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

from .index import Index, Media, RunStatus, Transcript
from .polish import Codex, CodexUnavailable
from .process_tree import start_queue_process
from .runtime import Settings
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
        [settings.python, "-m", "davinci_search", "cleanup", "--db", str(index.path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def request_stop(index: Index, token: str) -> bool:
    return index.change_run(token, STOPPABLE, "stopping")


def stop_requested(index: Index, token: str) -> bool:
    run = index.run_status()
    return bool(run and run.token == token and run.state == "stopping")


def worker_command(settings: Settings, index: Index, command: str, token: str) -> list[str]:
    return [settings.python, "-u", "-m", "davinci_search", command,
            "--config", str(settings.config_file), "--db", str(index.path), "--run", token]


def request_index(index: Index, settings: Settings, scan: Callable[[], list[Media]]) -> bool:
    with claim_worker(index.path) as acquired:
        if not acquired or starting(index.run_status()):
            return False
        token = uuid4().hex
        index.queue(scan(), token)
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
    except OSError:
        if not index.change_run(token, ("starting",), "failed"):
            index.change_run(token, ("stopping",), "stopped")
        raise
    return True


def run_queue(
    index: Index, transcribe: Callable[[Media], Transcript],
    polish: Callable[[dict[int, str]], dict[int, str]],
) -> None:
    polish_unavailable: str | None = None

    def check_revision(media: Media) -> None:
        if Media.from_path(media.key, media.name, media.path).revision != media.revision:
            raise RuntimeError("Media thay đổi hoặc mất kết nối. Hãy quét lại Media Pool.")

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
            index.record_error(media, polish_unavailable)
        except (OSError, ValueError, RuntimeError) as error:
            index.record_error(media, str(error))

    for media in index.pending("polish"):
        finish_polish(media)
    for media in index.pending("asr"):
        try:
            check_revision(media)
            transcript = transcribe(media)
            check_revision(media)
            index.save_transcript(media, transcript)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
            index.record_error(media, str(error))
            continue
        if transcript.segments:
            finish_polish(media)
    index.finish()


def run_worker(config_file: Path, database: Path, token: str) -> None:
    settings = Settings.load(config_file)
    index = Index(database)
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        with claim_worker(index.path) as acquired:
            if acquired:
                supervise(index, settings, token)
                return
        time.sleep(0.1)
    index.change_run(token, ("starting",), "failed")


def supervise(index: Index, settings: Settings, token: str) -> None:
    if not index.change_run(token, ("starting",), "running"):
        index.change_run(token, ("stopping",), "stopped")
        return
    outcome = "failed"
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
            if process.returncode == 0:
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
            index.change_run(token, ("stopping",), "stopped")


def run_queue_process(config_file: Path, database: Path, token: str) -> None:
    gate = sys.stdin.buffer
    if gate.read(1) != b"1":
        return
    if os.name != "nt":
        def watch_supervisor() -> None:
            os.read(gate.fileno(), 1)
            os.killpg(os.getpgrp(), signal.SIGKILL)

        Thread(target=watch_supervisor, daemon=True).start()
    settings = Settings.load(config_file)
    index = Index(database)
    scratch = scratch_path(database) / f"run-{token}"
    whisper = Whisper(settings, scratch, lambda backend, device, reason: index.report_backend(
        token, backend, device, reason
    ), lambda: index.end_recognition(token))
    run_queue(index, whisper.transcribe, Codex(settings.codex, scratch).polish)
