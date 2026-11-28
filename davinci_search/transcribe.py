"""Extract audio and run the multilingual whisper.cpp CLI on bounded audio chunks."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import wave

from .diagnostics import DiagnosticLog, ToolFailure, error_code
from .gpu import VulkanDevice, inspect_vulkan, load_gpu_preference, parse_vulkan_device
from .index import Media, Segment, Transcript
from .runtime import Settings, run_tool


AUDIO_RATE = 16000
CHUNK_SECONDS = 300
BackendReport = Callable[[str, str, str], None]
PROCESSING_AUDIO = "main: processing "
GPU_BACKEND = re.compile(r"whisper_backend_init_gpu: using ((Vulkan|CUDA)\d+) backend", re.I)


class GpuInterrupted(ToolFailure):
    def __init__(self, backend: str, device: str, reason: str, failure: str, code: int | None):
        super().__init__(reason, "whisper_vulkan", failure, code)
        self.backend = backend
        self.device = device


class Whisper:
    def __init__(self, settings: Settings, scratch_dir: Path, report: BackendReport, end: Callable[[], None],
                 diagnostics: DiagnosticLog):
        self.settings = settings
        self.scratch_dir = scratch_dir
        self.report = report
        self.end = end
        self.diagnostics = diagnostics
        self.gpu_preference = load_gpu_preference(settings.config_file) if settings.whisper_vulkan else None
        self.gpu_attempts: list[VulkanDevice | None] | None = None if settings.whisper_vulkan else []
        self.active_gpu: tuple[str, str] | None = None
        self.fallback = "" if settings.whisper_vulkan else "Chưa cấu hình bản Whisper Vulkan."

    def transcribe(self, media: Media) -> Transcript:
        settings = self.settings
        if not settings.whisper_model.is_file():
            raise ToolFailure("Không tìm thấy model whisper.cpp. Kiểm tra cấu hình cài đặt.",
                              "whisper", "model_missing")
        vad_model = settings.whisper_vad_model
        if vad_model is not None and not vad_model.is_file():
            raise ToolFailure("Không tìm thấy model Silero VAD. Kiểm tra cấu hình cài đặt.",
                              "whisper", "vad_model_missing")
        probe = run_tool([
            settings.ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
            "stream=index,channels:stream_disposition=default", "-of", "json", "-i", media.path,
        ], timeout=60)
        if probe.returncode:
            raise ToolFailure("FFprobe không đọc được media.", "ffprobe", "nonzero_exit", probe.returncode)
        streams = json.loads(probe.stdout).get("streams", [])
        if not streams:
            return Transcript([], 0, vad_model is not None)
        stream = next((s for s in streams if s.get("disposition", {}).get("default")), streams[0])

        with TemporaryDirectory(prefix="asr-", dir=self.scratch_dir) as scratch:
            directory = Path(scratch)
            extraction = run_tool([
                settings.ffmpeg, "-nostdin", "-v", "error", "-copyts", "-start_at_zero",
                "-i", media.path, "-map", f"0:{stream['index']}", "-ac", "1", "-ar",
                str(AUDIO_RATE), "-af", f"aresample={AUDIO_RATE}:async=1:first_pts=0",
                "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
                "-segment_format", "wav", "-reset_timestamps", "1", str(directory / "%08d.wav"),
            ])
            if extraction.returncode:
                raise ToolFailure("FFmpeg không trích xuất được audio.", "ffmpeg", "nonzero_exit", extraction.returncode)
            segments: list[Segment] = []
            sample_offset = 0
            for audio in sorted(directory.glob("*.wav")):
                with wave.open(str(audio), "rb") as info:
                    samples = info.getnframes()
                if samples:
                    segments.extend(self._recognize(audio, sample_offset, samples))
                sample_offset += samples
                audio.unlink()
            channels = int(stream.get("channels", 0)) if len(streams) == 1 else 0
            return Transcript(sorted(segments, key=lambda s: (s.start_ms, s.end_ms)), channels, vad_model is not None)

    def _recognize(self, audio: Path, sample_offset: int, samples: int) -> list[Segment]:
        output = audio.with_suffix("")
        json_output = audio.with_suffix(".json")
        arguments = [
            "-m", str(self.settings.whisper_model), "-f", str(audio), "-l", "auto",
            "-oj", "-of", str(output), "-t", str(min(8, os.cpu_count() or 4)),
        ]
        if self.settings.whisper_vad_model is not None:
            arguments.extend(["--vad", "--vad-model", str(self.settings.whisper_vad_model)])
        try:
            vulkan = self.settings.whisper_vulkan
            if vulkan:
                if self.gpu_attempts is None:
                    self.gpu_attempts = self._discover_gpu_attempts(vulkan)
                if self.gpu_attempts:
                    try:
                        return self._recognize_vulkan(vulkan, arguments, json_output, sample_offset, samples)
                    except GpuInterrupted:
                        raise
                    except ToolFailure as error:
                        self._use_cpu(str(error), error.reason, error.code)

            code = self._run_whisper(
                [self.settings.whisper_cpu, *arguments, "-ng"], WhisperLog(True, self.fallback)
            )
            if code or not json_output.is_file():
                raise ToolFailure(f"whisper.cpp không hoàn tất nhận dạng (mã {code}).", "whisper_cpu",
                                  "nonzero_exit" if code else "missing_output", code)
            return read_segments(json_output, sample_offset, samples)
        finally:
            self.end()

    def _discover_gpu_attempts(self, executable: str) -> list[VulkanDevice | None]:
        inventory = inspect_vulkan(executable)
        if self.gpu_preference is None:
            return list(inventory.devices) if inventory.selectable and inventory.devices else [None]
        if device := inventory.named_devices().get(self.gpu_preference):
            return [device]
        if inventory.selectable:
            self._use_cpu("The selected transcription GPU is unavailable.", "gpu_unavailable")
        else:
            self._use_cpu("The configured Whisper Vulkan runtime could not select the saved GPU.",
                          "gpu_selection_unavailable")
        return []

    def _recognize_vulkan(
        self, executable: str, arguments: list[str], output: Path, sample_offset: int, samples: int,
    ) -> list[Segment]:
        assert self.gpu_attempts
        for device in self.gpu_attempts:
            command = [executable, *arguments]
            if device is not None:
                command.extend(["--device", str(device.ordinal)])
            probe = WhisperLog(False, "", device)
            code = None
            try:
                code = self._run_whisper(command, probe)
                if code == 0 and not probe.reason and output.is_file():
                    result = read_segments(output, sample_offset, samples)
                    self.gpu_attempts = [device]
                    backend, name, _ = probe.backend()
                    if backend in ("Vulkan", "CUDA"):
                        self.active_gpu = backend, name
                    return result
                reason = probe.reason or (
                    f"Whisper Vulkan thoát với mã {code}." if code else "Whisper Vulkan không tạo transcript."
                )
                failure = probe.failure or ("nonzero_exit" if code else "missing_output")
            except FileNotFoundError as error:
                code, failure = error_code(error), "tool_not_found"
                reason = "Không tìm thấy bản Whisper Vulkan đã cấu hình."
            except OSError as error:
                code, failure = error_code(error), "os_error"
                reason = f"Không chạy được Whisper Vulkan (mã {code})." if code else "Không chạy được Whisper Vulkan."
            except (ValueError, KeyError, TypeError):
                failure = "invalid_output"
                reason = "Whisper Vulkan tạo transcript không hợp lệ."
            if self.active_gpu:
                raise GpuInterrupted(*self.active_gpu, reason, failure, code)
            output.unlink(missing_ok=True)
        raise ToolFailure(reason, "whisper_vulkan", failure, code)

    def _use_cpu(self, reason: str, failure: str, code: int | None = None) -> None:
        self.gpu_attempts = []
        self.fallback = reason
        self.diagnostics.record("backend_fallback", step="whisper_vulkan", reason=failure, code=code, backend="CPU")
        print(f"{reason} Dùng CPU cho lượt lập chỉ mục này.", flush=True)

    def _run_whisper(self, command: list[str], probe: WhisperLog) -> int:
        self.report("", "", probe.reason)
        with subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            assert process.stderr is not None
            for line in process.stderr:
                probe.observe(line)
                if line.startswith(PROCESSING_AUDIO):
                    probe.check_device()
                    if probe.reason and not probe.cpu:
                        process.kill()
                        break
                    self.report(*probe.backend())
            code = process.wait()
        if code == 0:
            probe.check_device()
            if not probe.reason or probe.cpu:
                self.report(*probe.backend())
        return code


class WhisperLog:
    """Read the selected backend from whisper.cpp startup, before recognition starts."""

    def __init__(self, cpu: bool, reason: str, expected: VulkanDevice | None = None):
        self.cpu = cpu
        self.reason = reason
        self.failure = ""
        self.selected: re.Match[str] | None = None
        self.devices: dict[str, str] = {}
        self.processing = False
        self.expected = expected

    def observe(self, line: str) -> None:
        if line.startswith(PROCESSING_AUDIO):
            self.processing = True
        if self.processing:
            # Silero starts on CPU after this point and emits its own backend logs.
            return
        if device := parse_vulkan_device(line):
            self.devices[f"vulkan{device.ordinal}"] = device.name
        selected = GPU_BACKEND.match(line)
        if selected:
            self.selected = selected
        if not self.cpu and line.startswith("whisper_backend_init_gpu:"):
            if "no GPU found" in line:
                self.reason = "Whisper Vulkan không phát hiện thiết bị GPU."
                self.failure = "gpu_not_found"
            elif "failed to initialize" in line:
                self.reason = "Whisper không khởi tạo được backend GPU."
                self.failure = "gpu_init_failed"

    def check_device(self) -> None:
        if self.expected is None or self.reason:
            return
        backend = f"vulkan{self.expected.ordinal}"
        if (self.selected is None or self.selected[1].casefold() != backend
                or self.devices.get(backend) != self.expected.name):
            self.reason = "Whisper did not initialize the requested transcription GPU."
            self.failure = "gpu_selection_mismatch"

    def backend(self) -> tuple[str, str, str]:
        if self.cpu or self.reason:
            return "CPU", "", self.reason
        if self.selected:
            device, api = self.selected.groups()
            return ("Vulkan" if api.casefold() == "vulkan" else "CUDA"), self.devices.get(device.casefold(), device), ""
        return "unknown", "", ""


def read_segments(output: Path, sample_offset: int, samples: int) -> list[Segment]:
    data = json.loads(output.read_text(encoding="utf-8"))
    offset_ms = round(sample_offset * 1000 / AUDIO_RATE)
    duration_ms = round(samples * 1000 / AUDIO_RATE)
    segments: list[Segment] = []
    for item in data["transcription"]:
        offsets = item["offsets"]
        start = max(0, int(offsets["from"]))
        end = min(duration_ms, int(offsets["to"]))
        text = item["text"].strip()
        if text and end > start:
            segments.append(Segment(offset_ms + start, offset_ms + end, text))
    return segments
