"""Read Windows hardware and driver capabilities without requiring either GPU SDK."""

from __future__ import annotations

import ctypes as c
from ctypes import wintypes
from dataclasses import dataclass
from io import TextIOWrapper
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from uuid import UUID

from .index import RunStatus
from .runtime import Settings
from .ui_text import english_diagnostic


UNKNOWN = "Unknown"


@dataclass(frozen=True)
class Adapter:
    name: str
    memory: int


@dataclass(frozen=True)
class Hardware:
    os: str
    cpu: str
    memory: int | None = None
    adapters: tuple[Adapter, ...] | None = None
    vulkan: bool | None = None
    cuda: bool | None = None


def inspect_hardware() -> Hardware:
    system = platform.platform()
    if os.name != "nt":
        return Hardware(system, platform.processor() or platform.machine())

    from ctypes import WinDLL
    import winreg

    cpu = platform.processor()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        pass
    memory = c.c_uint64()
    read_memory = WinDLL("kernel32").GetPhysicallyInstalledSystemMemory
    read_memory.argtypes = (c.POINTER(c.c_uint64),)
    return Hardware(
        f"Windows {platform.release()} · {platform.version()}", cpu,
        memory.value * 1024 if read_memory(c.byref(memory)) else None,
        read_adapters(), supports_vulkan(), supports_cuda(),
    )


class AdapterDesc(c.Structure):
    _fields_ = [
        ("description", c.c_wchar * 128), ("vendor", wintypes.UINT),
        ("device", wintypes.UINT), ("subsystem", wintypes.UINT), ("revision", wintypes.UINT),
        ("dedicated_video", c.c_size_t), ("dedicated_system", c.c_size_t), ("shared_system", c.c_size_t),
        ("luid", wintypes.DWORD * 2), ("flags", wintypes.UINT),
    ]


def com_method(interface: c.c_void_p, slot: int, result, *args):
    from ctypes import WINFUNCTYPE

    methods = c.cast(interface, c.POINTER(c.POINTER(c.c_void_p))).contents
    return WINFUNCTYPE(result, c.c_void_p, *args)(methods[slot])


def read_adapters() -> tuple[Adapter, ...] | None:
    from ctypes import WinDLL

    factory, adapter = c.c_void_p(), c.c_void_p()
    iid = (c.c_ubyte * 16).from_buffer_copy(UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    try:
        create = WinDLL("dxgi.dll").CreateDXGIFactory1
        create.argtypes = (c.c_void_p, c.POINTER(c.c_void_p))
        if create(iid, c.byref(factory)) < 0:
            return None
        # Slots follow IDXGIFactory1 and IDXGIAdapter1 in the Windows SDK.
        enumerate_adapter = com_method(factory, 12, c.c_long, wintypes.UINT, c.POINTER(c.c_void_p))
        adapters: dict[tuple[int, int], Adapter] = {}
        index = 0
        while (result := enumerate_adapter(factory, index, c.byref(adapter))) == 0:
            try:
                describe = com_method(adapter, 10, c.c_long, c.POINTER(AdapterDesc))
                desc = AdapterDesc()
                if describe(adapter, c.byref(desc)) == 0 and not desc.flags & 2:  # DXGI_ADAPTER_FLAG_SOFTWARE
                    adapters[tuple(desc.luid)] = Adapter(desc.description, desc.dedicated_video)
            finally:
                com_method(adapter, 2, wintypes.ULONG)(adapter)
            index += 1
        return tuple(adapters.values()) if result & 0xFFFFFFFF == 0x887A0002 else None  # DXGI_ERROR_NOT_FOUND
    except OSError:
        return None
    finally:
        if factory:
            com_method(factory, 2, wintypes.ULONG)(factory)


class VkInstanceCreateInfo(c.Structure):
    _fields_ = [
        ("type", c.c_int), ("next", c.c_void_p), ("flags", c.c_uint32),
        ("app", c.c_void_p), ("layer_count", c.c_uint32), ("layers", c.c_void_p),
        ("extension_count", c.c_uint32), ("extensions", c.c_void_p),
    ]


def supports_vulkan() -> bool | None:
    from ctypes import WinDLL

    try:
        loader = WinDLL("vulkan-1.dll", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
    except OSError:
        return False
    instance = c.c_void_p()
    try:
        loader.vkCreateInstance.argtypes = (c.POINTER(VkInstanceCreateInfo), c.c_void_p, c.POINTER(c.c_void_p))
        loader.vkDestroyInstance.argtypes = (c.c_void_p, c.c_void_p)
        loader.vkDestroyInstance.restype = None
        loader.vkEnumeratePhysicalDevices.argtypes = (c.c_void_p, c.POINTER(c.c_uint32), c.POINTER(c.c_void_p))
        loader.vkGetPhysicalDeviceProperties.argtypes = (c.c_void_p, c.c_void_p)
        loader.vkGetPhysicalDeviceProperties.restype = None
        result = loader.vkCreateInstance(c.byref(VkInstanceCreateInfo(1)), None, c.byref(instance))
        if result:
            return False if result == -9 else None  # VK_ERROR_INCOMPATIBLE_DRIVER
        count = c.c_uint32()
        if loader.vkEnumeratePhysicalDevices(instance, c.byref(count), None):
            return None
        if not count.value:
            return False
        devices = (c.c_void_p * count.value)()
        if loader.vkEnumeratePhysicalDevices(instance, c.byref(count), devices):
            return None
        # The fixed Vulkan 1.0 properties record fits in this aligned buffer.
        # Its fifth uint32_t is deviceType. Exclude CPU/software devices.
        properties = (c.c_uint64 * 128)()
        words = c.cast(properties, c.POINTER(c.c_uint32))
        for device in devices[:count.value]:
            loader.vkGetPhysicalDeviceProperties(device, properties)
            if words[4] in (1, 2, 3):  # Integrated, discrete, virtual GPU
                return True
        return False
    except AttributeError:
        return None
    finally:
        if instance:
            loader.vkDestroyInstance(instance, None)


def supports_cuda() -> bool | None:
    from ctypes import WinDLL

    try:
        driver = WinDLL("nvcuda.dll", winmode=0x800)
    except OSError:
        return False
    try:
        result = driver.cuInit(0)
        if result:
            return False if result == 100 else None  # CUDA_ERROR_NO_DEVICE
        count = c.c_int()
        driver.cuDeviceGetCount.argtypes = (c.POINTER(c.c_int),)
        return count.value > 0 if driver.cuDeviceGetCount(c.byref(count)) == 0 else None
    except AttributeError:
        return None


def capability(supported: bool | None) -> str:
    return {True: "Supported", False: "Support not detected", None: UNKNOWN}[supported]


def whisper_install(executable: str | None) -> str:
    if not executable:
        return "Not configured"
    return "Configured, executable found" if shutil.which(executable) else "Configured, executable missing"


def describe_hardware(hardware: Hardware, settings: Settings) -> str:
    def gib(size: int | None) -> str:
        return f"{size / 1024**3:.1f} GiB" if size is not None else UNKNOWN

    gpus = dict.fromkeys(
        f"{adapter.name} · {gib(adapter.memory)} dedicated VRAM" for adapter in hardware.adapters or ()
    )
    gpu = ("\n" + " " * 19).join(gpus) if gpus else ("No GPU detected" if hardware.adapters == () else UNKNOWN)
    return "\n".join((
        f"{'Operating system':18} {hardware.os or UNKNOWN}",
        f"{'CPU':18} {hardware.cpu or UNKNOWN}",
        f"{'RAM':18} {gib(hardware.memory)}",
        f"{'GPU':18} {gpu}",
        "",
        f"{'Backend':12} {'Hardware / driver':22} Whisper runtime",
        f"{'CPU':12} {capability(True):22} {whisper_install(settings.whisper_cpu)}",
        f"{'Vulkan':12} {capability(hardware.vulkan):22} {whisper_install(settings.whisper_vulkan)}",
        f"{'CUDA':12} {capability(hardware.cuda):22} {whisper_install(None)}",
    ))


def describe_recognition(run: RunStatus | None, active: bool) -> str:
    if run is None or (not run.backend and not (active and run.recognizing)):
        return "No transcription run yet."
    backend = {"CPU": "CPU", "Vulkan": "GPU via Vulkan", "CUDA": "GPU via CUDA"}.get(run.backend, UNKNOWN)
    if run.device:
        backend += f" · {run.device}"
    if active and run.recognizing and not run.backend:
        line = "Starting Whisper."
    elif active and run.recognizing:
        line = f"Transcribing with {backend}."
    else:
        line = f"Last transcription backend · {backend}"
    return f"{line}\n{english_diagnostic(run.fallback)}" if run.fallback else line


def start_hardware_probe(settings: Settings) -> subprocess.Popen[str]:
    # GPU driver initialization runs outside Resolve even if a driver fails.
    root = Path(__file__).resolve().parent.parent
    return subprocess.Popen(
        [settings.python, "-X", "utf8", "-m", "davinci_search.hardware", str(settings.config_file)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=root,
        encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def hardware_fallback(settings: Settings) -> str:
    return describe_hardware(Hardware(platform.platform(), platform.processor()), settings)


if __name__ == "__main__":
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(describe_hardware(inspect_hardware(), Settings.load(Path(sys.argv[1]))))
