"""Report application prerequisites without revealing authentication details."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from subprocess import TimeoutExpired
import sys

from .runtime import Settings, run_tool


def codex_status(command: str, *, check_login: bool) -> str:
    executable = shutil.which(command)
    if not executable:
        return "Missing"
    if check_login:
        try:
            result = run_tool([executable, "login", "status"], timeout=3)
        except (OSError, TimeoutExpired):
            pass
        else:
            if result.returncode == 0:
                return "Found · signed in"
            for output in (result.stdout, result.stderr):
                if any(line.strip().rstrip(".").casefold() == "not logged in" for line in output.splitlines()):
                    return "Found · sign-in required"
    return "Found · sign-in could not be verified"


def describe_dependencies(settings: Settings, *, checked: bool) -> str:
    python = "Missing"
    if shutil.which(settings.python):
        python = "Found · version not checked"
        if checked:
            bits = 64 if sys.maxsize > 2**32 else 32
            version = ".".join(map(str, sys.version_info[:3]))
            compatible = sys.version_info >= (3, 10) and bits == 64
            python = f"{'Ready' if compatible else 'Requires Python 3.10+ (64-bit)'} · {version}, {bits}-bit"

    powershell = "Not applicable"
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        builtin = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
        powershell = "Found" if builtin.is_file() or shutil.which("powershell.exe") or shutil.which("pwsh.exe") else "Missing"

    codex = codex_status(settings.codex, check_login=checked)
    vad = "Not configured"
    if settings.whisper_vad_model is not None:
        vad = "Found" if settings.whisper_vad_model.is_file() else "Missing"
    rows = (
        ("Python", python),
        ("PowerShell", powershell),
        ("FFmpeg", "Found" if shutil.which(settings.ffmpeg) else "Missing"),
        ("FFprobe", "Found" if shutil.which(settings.ffprobe) else "Missing"),
        ("Whisper model", "Found" if settings.whisper_model.is_file() else "Missing"),
        ("Silero VAD", vad),
        ("Codex CLI", codex),
    )
    report = "\n".join(("Dependencies", *(f"{name:18} {status}" for name, status in rows)))
    if codex == "Missing":
        report += "\n\nInstall and sign in to Codex CLI to enable automatic transcript polishing.\nRaw transcripts are still searchable."
    elif codex == "Found · sign-in required":
        report += "\n\nSign in to Codex CLI to enable automatic transcript polishing."
    return report
