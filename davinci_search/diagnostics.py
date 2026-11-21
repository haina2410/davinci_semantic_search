"""Record support events and export them without reading project data or raw logs."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
from tempfile import NamedTemporaryFile
from time import time
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

if not __package__:
    # The Windows export shortcut runs this file in an isolated interpreter.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davinci_search.runtime import python_module_command


RETENTION_SECONDS = 72 * 60 * 60
MAX_EVENTS = 5000
STORE = "diagnostics.sqlite3"


class ToolFailure(RuntimeError):
    def __init__(self, message: str, step: str, reason: str, code: int | None = None):
        super().__init__(message)
        self.step, self.reason, self.code = step, reason, code


def error_code(error: BaseException) -> int | None:
    if isinstance(error, OSError):
        return getattr(error, "winerror", None) or error.errno
    return None


def iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class DiagnosticLog:
    install_dir: Path
    project: str = ""
    run: str = ""

    def record(self, event: str, *, step: str | None = None, reason: str | None = None,
               error: str | None = None, code: int | None = None, backend: str | None = None,
               version: str | None = None) -> None:
        # Callers pass event names and codes, never exception messages or tool output.
        now = time()
        details = {"time": iso_time(now), "event": event}
        for key, value in (("run", self.run), ("step", step), ("reason", reason), ("error", error),
                           ("backend", backend), ("version", version)):
            if value and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
                details[key] = value
        if type(code) is int:
            details["code"] = code
        try:
            with closing(sqlite3.connect(self.install_dir / STORE, timeout=0.25)) as db, db:
                db.execute("""CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, created REAL NOT NULL, project TEXT NOT NULL,
                    details TEXT NOT NULL
                )""")
                event_id = db.execute("INSERT INTO events(created, project, details) VALUES (?, ?, ?)",
                                      (now, self.project, json.dumps(details))).lastrowid
                if event_id is not None:
                    db.execute("DELETE FROM events WHERE id <= ? OR created < ?",
                               (event_id - MAX_EVENTS, now - RETENTION_SECONDS))
        except (OSError, sqlite3.Error):
            # Diagnostics should never prevent indexing or hide the original failure.
            pass

    def failure(self, event: str, step: str, error: BaseException) -> None:
        reason = None
        code = error_code(error)
        if isinstance(error, ToolFailure):
            step, reason, code = error.step, error.reason, error.code
        self.record(event, step=step, reason=reason, error=type(error).__name__, code=code)


def read_events(install_dir: Path, project: str, latest: bool) -> list[dict]:
    store = install_dir / STORE
    if not store.exists():
        return []
    now = time()
    period = (now - RETENTION_SECONDS, now)
    with closing(sqlite3.connect(store.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
        db.execute("BEGIN")
        if latest:
            row = db.execute("""SELECT project FROM events WHERE project <> ''
                                AND created BETWEEN ? AND ? ORDER BY id DESC LIMIT 1""", period).fetchone()
            project = row[0] if row else ""
        rows = db.execute("""SELECT details FROM events WHERE (project = '' OR project = ?)
                             AND created BETWEEN ? AND ? ORDER BY id""", (project, *period))
        return [json.loads(details) for (details,) in rows]


def gpu_names(python: str) -> list[str] | None:
    if os.name != "nt":
        return None
    # Keep graphics-driver calls outside the Resolve process.
    try:
        result = subprocess.run(
            python_module_command(python, "davinci_search.hardware", "--adapters"),
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            encoding="utf-8", timeout=3, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        names = json.loads(result.stdout)
        if isinstance(names, list):
            return [name for name in names if isinstance(name, str)
                    and re.fullmatch(r"[\w .+(),®™-]{1,128}", name)]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def export_bundle(install_dir: Path, project: str = "", *, latest: bool = False,
                  python: str = sys.executable) -> Path:
    try:
        version = (install_dir / "version.txt").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        version = ""
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-(?:beta|preview)\.\d+)?|development", version):
        version = "unknown"
    try:
        events = read_events(install_dir, project, latest)
        events_available = True
    except (OSError, ValueError, sqlite3.Error):
        events = []
        events_available = False
    metadata = {
        "format": 1,
        "exported_at": iso_time(time()),
        "history_hours": RETENTION_SECONDS // 3600,
        "plugin_version": version,
        "resolve_version": next((event["version"] for event in reversed(events)
                                 if event["event"] == "resolve_connected" and "version" in event), None),
        "operating_system": f"{platform.system()} {platform.release()} ({platform.version()})",
        "gpu_adapters": gpu_names(python),
        "events_available": events_available,
    }
    directory = install_dir / "exports"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"davinci-semantic-search-logs-{stamp}-{uuid4().hex[:8]}.zip"
    with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as temporary:
        pending = Path(temporary.name)
    try:
        with ZipFile(pending, "w", ZIP_DEFLATED) as archive:
            archive.writestr("system.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("events.jsonl", "".join(json.dumps(event) + "\n" for event in events))
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)
    return path


def open_export_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path.parent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export logs for DaVinci Semantic Search")
    parser.add_argument("install_dir", type=Path)
    args = parser.parse_args()
    try:
        path = export_bundle(args.install_dir, latest=True)
    except (OSError, RuntimeError) as error:
        print(f"Could not export logs: {error}", file=sys.stderr)
        if sys.stdin.isatty():
            input("Press Enter to close.")
        raise SystemExit(1) from None
    print(f"Exported {path}")
    try:
        open_export_folder(path)
    except OSError:
        if sys.stdin.isatty():
            input("Could not open the export folder. Press Enter to close.")


if __name__ == "__main__":
    main()
