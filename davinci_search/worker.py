"""Own the queue across Resolve sessions and run ASR and Codex outside Resolve."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import time
from typing import Callable, Generator

from .index import Index, Media, Segment
from .polish import Codex, PolishError
from .runtime import Settings
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


def request_index(index: Index, settings: Settings, scan: Callable[[], list[Media]]) -> bool:
    with claim_worker(index.path) as acquired:
        if not acquired:
            return False
        index.queue(scan())
        root = Path(__file__).resolve().parent.parent
        log_file = index.path.with_name("worker.log")
        with log_file.open("ab") as log:
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            subprocess.Popen(
                [settings.python, "-u", "-m", "davinci_search", "worker",
                 "--config", str(settings.config_file), "--db", str(index.path)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=root,
                creationflags=flags, start_new_session=os.name != "nt",
            )
    return True


def run_queue(
    index: Index, transcribe: Callable[[Media], list[Segment]],
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
        except PolishError as error:
            polish_unavailable = str(error)
            index.record_error(media, polish_unavailable)
        except (OSError, ValueError, RuntimeError) as error:
            index.record_error(media, str(error))

    for media in index.pending("polish"):
        finish_polish(media)
    for media in index.pending("asr"):
        try:
            check_revision(media)
            segments = transcribe(media)
            check_revision(media)
            index.save_transcript(media, segments)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
            index.record_error(media, str(error))
            continue
        if segments:
            finish_polish(media)
    index.finish()


def run_worker(config_file: Path, database: Path) -> None:
    settings = Settings.load(config_file)
    index = Index(database)
    for _ in range(50):
        with claim_worker(index.path) as acquired:
            if acquired:
                if index.status().requested:
                    scratch = index.path.with_name("scratch")
                    scratch.mkdir(exist_ok=True)
                    run_queue(index, Whisper(settings, scratch).transcribe, Codex(settings.codex, scratch).polish)
                return
        time.sleep(0.1)
