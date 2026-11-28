"""Discover the Vulkan devices exposed by the configured Whisper CLI."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from subprocess import TimeoutExpired
from tempfile import NamedTemporaryFile

from .runtime import run_tool


DEVICE_LINE = re.compile(r"^ggml_vulkan:\s*(\d+) = ([^|]+) \([^|]*\) \|\s*(?:uma: ([01]) \|)?")
DEVICE_OPTION = re.compile(r"^\s*(?:-dev\s+\S+,\s+)?--device\s+", re.M)


@dataclass(frozen=True)
class VulkanDevice:
    ordinal: int
    name: str
    integrated: bool | None


def parse_vulkan_device(line: str) -> VulkanDevice | None:
    match = DEVICE_LINE.match(line)
    if match:
        ordinal, name, uma = match.groups()
        return VulkanDevice(int(ordinal), name, None if uma is None else uma == "1")
    return None


@dataclass(frozen=True)
class VulkanInventory:
    selectable: bool = False
    devices: tuple[VulkanDevice, ...] = ()

    def named_devices(self) -> dict[str, VulkanDevice]:
        if not self.selectable:
            return {}
        counts = Counter(device.name for device in self.devices)
        # Whisper's output provides no persistent ID for GPUs with identical names.
        return {device.name: device for device in self.devices if counts[device.name] == 1}


def inspect_vulkan(executable: str | None) -> VulkanInventory:
    if not executable:
        return VulkanInventory()
    try:
        result = run_tool([executable, "--help"], timeout=3)
    except (OSError, TimeoutExpired):
        return VulkanInventory()
    output = f"{result.stderr}\n{result.stdout}"
    devices = {}
    for line in output.splitlines():
        if device := parse_vulkan_device(line):
            devices[device.ordinal] = device
    # GGML reports uma=1 for integrated GPUs. Keep Whisper's order within each group.
    ordered = sorted(devices.values(), key=lambda device: (device.integrated is not False, device.ordinal))
    return VulkanInventory(bool(DEVICE_OPTION.search(output)), tuple(ordered))


def load_gpu_preference(config_file: Path) -> str | None:
    try:
        value = json.loads(config_file.with_name("transcription-gpu.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if value is None or (isinstance(value, str) and value):
        return value
    raise ValueError("The saved transcription GPU preference is invalid.")


def save_gpu_preference(config_file: Path, name: str | None) -> None:
    # This installation-local preference stays independent of updates to settings.json.
    path = config_file.with_name("transcription-gpu.json")
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(name, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
