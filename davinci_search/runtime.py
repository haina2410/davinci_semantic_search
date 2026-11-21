"""Runtime configuration shared by the Resolve script and index worker."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class Settings:
    config_file: Path
    data_dir: Path
    python: str
    ffmpeg: str
    ffprobe: str
    whisper_cpu: str
    whisper_vulkan: str | None
    whisper_model: Path
    codex: str

    @classmethod
    def load(cls, config_file: Path) -> Settings:
        values = json.loads(config_file.read_text(encoding="utf-8-sig"))
        return cls(
            config_file.resolve(), Path(values["data_dir"]), values["python"],
            values["ffmpeg"], values["ffprobe"], values["whisper_cpu"],
            values.get("whisper_vulkan"), Path(values["whisper_model"]), values["codex"],
        )


def python_module_command(python: str, module: str, *arguments: str) -> list[str]:
    # ResolvePython ignores the working directory and PYTHONPATH when importing.
    root = Path(__file__).resolve().parent.parent
    bootstrap = (
        "import runpy, sys\n"
        "sys.path.insert(0, sys.argv.pop(1))\n"
        "runpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True)"
    )
    return [python, "-u", "-X", "utf8", "-c", bootstrap, str(root), module, *arguments]


def run_tool(
    command: list[str], *, timeout: float | None = None, cwd: Path | None = None,
    prompt: str | None = None, quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, input=prompt, stdin=subprocess.DEVNULL if prompt is None else None,
        capture_output=not quiet, stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
