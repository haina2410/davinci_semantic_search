"""Clean project-owned temporary files under the project worker lock."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
from subprocess import TimeoutExpired

from .runtime import run_tool


RUN_DIRECTORY = re.compile(r"run-[a-f0-9]{32}")
STAGE_PREFIXES = ("asr-", "codex-")


def plain_directory(path: Path) -> bool:
    attributes = path.lstat()
    return stat.S_ISDIR(attributes.st_mode) and not (
        getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def scratch_path(database: Path) -> Path:
    return database.parent.resolve() / "scratch"


def run_scratch(database: Path, token: str) -> Path:
    name = f"run-{token}"
    if not RUN_DIRECTORY.fullmatch(name):
        raise ValueError("Mã lượt lập chỉ mục không hợp lệ.")
    root = scratch_path(database)
    root.mkdir(exist_ok=True)
    if not plain_directory(root):
        raise OSError("Thư mục tạm không phải thư mục cục bộ của project.")
    directory = root / name
    directory.mkdir()
    return directory


def legacy_tool_paths() -> str | None:
    if os.name != "nt":
        return None  # This migration is for the previous Windows installer.
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process -ErrorAction Stop | "
        "Where-Object CommandLine | Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress"
    )
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = run_tool([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script], timeout=5)
        if not result.returncode:
            lines = json.loads(result.stdout) if result.stdout.strip() else []
            return ("\n".join(lines) if isinstance(lines, list) else lines).casefold().replace("/", "\\")
    except (OSError, ValueError, TimeoutExpired):
        pass
    return None


def cleanup_scratch(database: Path, token: str | None = None) -> bool:
    """Return whether cleanup must be retried. The caller holds the worker lock."""
    root = scratch_path(database)
    try:
        if not plain_directory(root) or root.resolve() != root:
            return True
        targets = [
            path for path in root.iterdir()
            if (path.name == f"run-{token}" if token else
                RUN_DIRECTORY.fullmatch(path.name) or path.name.startswith(STAGE_PREFIXES))
        ]
    except FileNotFoundError:
        return False
    except OSError:
        return True

    legacy = [path for path in targets if path.name.startswith(STAGE_PREFIXES)]
    commands = legacy_tool_paths() if legacy else ""
    pending = False
    for path in targets:
        try:
            if path in legacy and (commands is None or str(path).casefold() in commands):
                pending = True
            elif plain_directory(path) and path.resolve() == path and path.parent == root:
                shutil.rmtree(path)
            else:
                pending = True
        except FileNotFoundError:
            pending |= path.exists()
        except OSError:
            pending = True
    return pending
