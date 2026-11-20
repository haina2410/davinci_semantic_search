"""Contain one queue's tools so cancellation and crashes release their files."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import time


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4), ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD), ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


@contextmanager
def windows_job(process: subprocess.Popen[bytes]):
    from ctypes import WinDLL, WinError

    kernel = WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    )
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise WinError(ctypes.get_last_error())
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise WinError(ctypes.get_last_error())
        handle = kernel.OpenProcess(0x0101, False, process.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not handle:
            raise WinError(ctypes.get_last_error())
        try:
            if not kernel.AssignProcessToJobObject(job, handle):
                raise WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        try:
            yield
        finally:
            if not kernel.TerminateJobObject(job, 1):
                raise WinError(ctypes.get_last_error())
            accounting = Accounting()
            while True:
                if not kernel.QueryInformationJobObject(
                    job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
                ):
                    raise WinError(ctypes.get_last_error())
                if not accounting.active_processes:
                    break
                time.sleep(0.05)
    finally:
        kernel.CloseHandle(job)


@contextmanager
def process_group(process: subprocess.Popen[bytes]):
    try:
        yield
    finally:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@contextmanager
def start_queue_process(command: list[str]):
    # The child waits on stdin before launching any tool. A gated start makes
    # assignment to the Windows Job atomic with respect to its descendants.
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    pipe = process.stdin
    assert pipe is not None
    try:
        contain = windows_job if os.name == "nt" else process_group
        with contain(process):
            pipe.write(b"1")
            pipe.flush()
            yield process
    finally:
        try:
            pipe.close()
        except BrokenPipeError:
            pass
        if process.poll() is None:
            process.kill()
        process.wait()
