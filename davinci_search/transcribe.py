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

from .index import Media, Segment, Transcript
from .runtime import Settings, run_tool


AUDIO_RATE = 16000
CHUNK_SECONDS = 300
BackendReport = Callable[[str, str, str], None]
GPU_BACKEND = re.compile(r"whisper_backend_init_gpu: using ((Vulkan|CUDA)\d+) backend", re.I)
VULKAN_DEVICE = re.compile(r"ggml_vulkan:\s*(\d+) = (.+?) \(.+?\) \|")


class Whisper:
    def __init__(self, settings: Settings, scratch_dir: Path, report: BackendReport, end: Callable[[], None]):
        self.settings = settings
        self.scratch_dir = scratch_dir
        self.report = report
        self.end = end
        self.vulkan = settings.whisper_vulkan
        self.fallback = "" if self.vulkan else "Chưa cấu hình bản Whisper Vulkan."

    def transcribe(self, media: Media) -> Transcript:
        settings = self.settings
        if not settings.whisper_model.is_file():
            raise RuntimeError("Không tìm thấy model whisper.cpp. Kiểm tra cấu hình cài đặt.")
        probe = run_tool([
            settings.ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
            "stream=index,channels:stream_disposition=default", "-of", "json", "-i", media.path,
        ], timeout=60)
        if probe.returncode:
            raise RuntimeError("FFprobe không đọc được media.")
        streams = json.loads(probe.stdout).get("streams", [])
        if not streams:
            return Transcript([], 0)
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
                raise RuntimeError("FFmpeg không trích xuất được audio.")
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
            return Transcript(sorted(segments, key=lambda s: (s.start_ms, s.end_ms)), channels)

    def _recognize(self, audio: Path, sample_offset: int, samples: int) -> list[Segment]:
        output = audio.with_suffix("")
        json_output = audio.with_suffix(".json")
        arguments = [
            "-m", str(self.settings.whisper_model), "-f", str(audio), "-l", "auto",
            "-oj", "-of", str(output), "-t", str(min(8, os.cpu_count() or 4)),
        ]
        try:
            if self.vulkan:
                probe = WhisperLog(False, "")
                try:
                    code = self._run_whisper([self.vulkan, *arguments], probe)
                    if code == 0 and json_output.is_file():
                        result = read_segments(json_output, sample_offset, samples)
                        if probe.reason:
                            self._use_cpu(probe.reason)
                        return result
                    reason = probe.reason or (
                        f"Whisper Vulkan thoát với mã {code}." if code else "Whisper Vulkan không tạo transcript."
                    )
                except FileNotFoundError:
                    reason = "Không tìm thấy bản Whisper Vulkan đã cấu hình."
                except OSError as error:
                    code = getattr(error, "winerror", None) or error.errno
                    reason = f"Không chạy được Whisper Vulkan (mã {code})." if code else "Không chạy được Whisper Vulkan."
                except (ValueError, KeyError, TypeError):
                    reason = "Whisper Vulkan tạo transcript không hợp lệ."
                self._use_cpu(reason)
                json_output.unlink(missing_ok=True)

            code = self._run_whisper(
                [self.settings.whisper_cpu, *arguments, "-ng"], WhisperLog(True, self.fallback)
            )
            if code or not json_output.is_file():
                raise RuntimeError(f"whisper.cpp không hoàn tất nhận dạng (mã {code}).")
            return read_segments(json_output, sample_offset, samples)
        finally:
            self.end()

    def _use_cpu(self, reason: str) -> None:
        self.vulkan = None
        self.fallback = reason
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
                if line.startswith("main: processing "):
                    self.report(*probe.backend())
            code = process.wait()
        if code == 0:
            self.report(*probe.backend())
        return code


class WhisperLog:
    """Read the selected backend from whisper.cpp startup, before recognition starts."""

    def __init__(self, cpu: bool, reason: str):
        self.cpu = cpu
        self.reason = reason
        self.selected: re.Match[str] | None = None
        self.devices: dict[str, str] = {}

    def observe(self, line: str) -> None:
        device = VULKAN_DEVICE.match(line)
        if device:
            self.devices[f"Vulkan{device[1]}".casefold()] = device[2]
        selected = GPU_BACKEND.match(line)
        if selected:
            self.selected = selected
        if not self.cpu and line.startswith("whisper_backend_init_gpu:"):
            if "no GPU found" in line:
                self.reason = "Whisper Vulkan không phát hiện thiết bị GPU."
            elif "failed to initialize" in line:
                self.reason = "Whisper không khởi tạo được backend GPU."

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
