"""Extract audio and run the multilingual whisper.cpp CLI on bounded audio chunks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import wave

from .index import Media, Segment, Transcript
from .runtime import Settings, run_tool


AUDIO_RATE = 16000
CHUNK_SECONDS = 300


class Whisper:
    def __init__(self, settings: Settings, scratch_dir: Path):
        self.settings = settings
        self.scratch_dir = scratch_dir
        self.vulkan = settings.whisper_vulkan

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
            "-oj", "-of", str(output), "-np", "-t", str(min(8, os.cpu_count() or 4)),
        ]
        if self.vulkan:
            try:
                attempt = run_tool([self.vulkan, *arguments])
                if attempt.returncode == 0 and json_output.is_file():
                    return read_segments(json_output, sample_offset, samples)
            except (OSError, ValueError, KeyError):
                pass
            self.vulkan = None
            json_output.unlink(missing_ok=True)
            print("Vulkan không thành công, dùng CPU cho lượt lập chỉ mục này.", flush=True)

        attempt = run_tool([self.settings.whisper_cpu, *arguments, "-ng"])
        if attempt.returncode or not json_output.is_file():
            raise RuntimeError(f"whisper.cpp không hoàn tất nhận dạng (mã {attempt.returncode}).")
        return read_segments(json_output, sample_offset, samples)


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
