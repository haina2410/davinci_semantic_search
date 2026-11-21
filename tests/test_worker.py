from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from davinci_search.dependencies import describe_dependencies
from davinci_search.hardware import Adapter, Hardware, describe_hardware, describe_recognition
from davinci_search.index import Index, Media
from davinci_search.runtime import Settings
from davinci_search.transcribe import WhisperLog
from davinci_search.worker import recover_scratch, request_index, request_stop, worker_active, worker_command


TOOL = """
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

tool, *args = sys.argv[1:]
root = Path(__file__).parent
if tool == "hold":
    with open(args[0], "ab"):
        (root / "ready").touch()
        time.sleep(30)
    sys.exit()

def hold(path):
    if (root / "hold").read_text() == tool:
        subprocess.run([sys.executable, __file__, "hold", str(path)])

if tool == "ffprobe":
    print(json.dumps({"streams": [{"index": 0, "channels": 1}]}))
elif tool == "ffmpeg":
    output = Path(args[-1].replace("%08d", "00000000"))
    hold(output)
    with wave.open(str(output), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\\0" * 3200)
elif tool == "whisper":
    output = Path(args[args.index("-of") + 1] + ".json")
    print("main: processing 'fixture' ...", file=sys.stderr, flush=True)
    hold(output)
    text = "hello world"
    if "--vad" in args and Path(args[args.index("--vad-model") + 1]) == root / "silero.bin":
        text = "speech from VAD"
    output.write_text(json.dumps({"transcription": [
        {"offsets": {"from": 5, "to": 80}, "text": text}
    ]}))
elif tool == "codex":
    output = Path(args[args.index("--output-last-message") + 1])
    hold(output)
    output.write_text(json.dumps({"segments": [{"id": 0, "text": "Hello world."}]}))
"""


class WorkerTests(unittest.TestCase):
    def setUp(self):
        cache = Path(".cache").resolve()
        cache.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.cleanup_fixture, temporary)
        self.root = Path(temporary.name)
        self.python = sys.executable

    def cleanup_fixture(self, directory):
        # A detached supervisor may be exiting after it released the project lock.
        deadline = time.monotonic() + 2
        while True:
            try:
                directory.cleanup()
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def project(self, name, hold, *, missing_vulkan=False, vad=False):
        folder = self.root / name
        index = Index(folder / "index.sqlite3")
        (folder / "hold").write_text(hold)
        script = folder / "tool.py"
        script.write_text(TOOL)
        tools = {}
        for stage in ("ffmpeg", "ffprobe", "whisper", "codex"):
            if os.name == "nt":
                launcher = folder / f"{stage}.cmd"
                launcher.write_text(f'@echo off\n"{self.python}" "{script}" {stage} %*\n')
            else:
                launcher = folder / stage
                launcher.write_text(f"#!/bin/sh\nexec {shlex.quote(self.python)} {shlex.quote(str(script))} "
                                    f'{stage} "$@"\n')
                launcher.chmod(0o700)
            tools[stage] = str(launcher)
        source = folder / "source.wav"
        source.touch()
        config = folder / "settings.json"
        vad_model = folder / "silero.bin"
        if vad:
            vad_model.touch()
        config.write_text(json.dumps({
            "data_dir": str(folder), "python": self.python, "whisper_model": str(source),
            "whisper_cpu": tools["whisper"], "ffprobe": tools["ffprobe"], "ffmpeg": tools["ffmpeg"],
            "codex": tools["codex"], "whisper_vulkan": str(folder / "missing.exe") if missing_vulkan else None,
            "whisper_vad_model": str(vad_model) if vad else None,
        }))
        self.addCleanup(self.stop_if_running, index)
        return index, Settings.load(config), Media.from_path("source", "Fixture", str(source))

    def wait_for(self, check, *, index):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.05)
        log = index.path.with_name("worker.log")
        details = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        self.fail(f"Worker timed out: {index.run_status()} · {index.status()}\n{details}")

    def wait_state(self, index, state):
        def matches():
            run = index.run_status()
            return run and run.state == state and not worker_active(index) and run
        return self.wait_for(matches, index=index)

    def start(self, index, settings, media):
        self.assertTrue(request_index(index, settings, lambda: [media]))
        return self.wait_for(index.path.with_name("ready").exists, index=index)

    def stop_if_running(self, index):
        run = index.run_status()
        if run and request_stop(index, run.token):
            self.wait_state(index, "stopped")

    def test_index_uses_vad_when_whisper_falls_back_to_cpu(self):
        index, settings, media = self.project("silero", "", missing_vulkan=True, vad=True)
        self.assertTrue(request_index(index, settings, lambda: [media]))
        self.wait_state(index, "finished")
        self.assertEqual(index.raw(media), {0: "speech from VAD"})

    def test_silero_cpu_startup_does_not_override_whisper_vulkan(self):
        log = WhisperLog(False, "")
        log.observe("whisper_backend_init_gpu: using Vulkan0 backend")
        log.observe("main: processing 'fixture' ...")
        log.observe("whisper_backend_init_gpu: no GPU found")
        self.assertEqual(log.backend(), ("Vulkan", "Vulkan0", ""))

    def test_stop_each_stage_preserves_the_latest_checkpoint_and_releases_scratch(self):
        for stage in ("ffmpeg", "whisper", "codex"):
            with self.subTest(stage=stage):
                index, settings, media = self.project(stage, stage)
                self.start(index, settings, media)
                token = index.run_status().token
                self.assertTrue(request_stop(index, token))
                self.wait_state(index, "stopped")
                self.assertTrue(index.status().requested)
                expected = "polish" if stage == "codex" else "asr"
                self.assertEqual(index.status().phases, {expected: 1})
                self.assertEqual(bool(index.searchable()), stage == "codex")
                self.assertFalse(list(index.path.with_name("scratch").iterdir()))
                self.assertTrue(Path(media.path).exists())

    def test_stop_is_project_scoped_and_vulkan_fallback_is_visible_during_recognition(self):
        a, settings_a, media_a = self.project("a", "whisper", missing_vulkan=True)
        b, settings_b, media_b = self.project("b", "whisper")
        self.start(a, settings_a, media_a)
        self.start(b, settings_b, media_b)
        run = a.run_status()
        self.assertEqual(describe_recognition(run, True), "Transcribing with CPU.\n"
                         "The configured Whisper Vulkan executable was not found.")
        self.assertIn("Không tìm thấy bản Whisper Vulkan", run.fallback)
        self.assertFalse(request_index(a, settings_a, lambda: [media_a]))
        self.assertTrue(request_stop(a, run.token))
        self.wait_state(a, "stopped")
        self.assertTrue(worker_active(b))
        self.assertEqual(b.run_status().state, "running")
        a.path.with_name("hold").write_text("")
        self.assertTrue(request_index(a, settings_a, lambda: [media_a]))
        self.assertFalse(request_stop(a, run.token))
        self.wait_state(a, "finished")
        self.assertEqual(a.searchable()[0].polished, "Hello world.")

    def test_crashed_supervisor_releases_its_tree_and_recovers_only_project_scratch(self):
        index, settings, media = self.project("crash", "codex")
        token = uuid4().hex
        index.queue([media], token)
        with subprocess.Popen(worker_command(settings, index, "worker", token)) as supervisor:
            try:
                self.wait_for(index.path.with_name("ready").exists, index=index)
            finally:
                supervisor.kill()
                supervisor.wait(timeout=5)
        root = index.path.with_name("scratch")
        (root / "unrelated").mkdir()
        if os.name == "nt":
            with (root / f"run-{token}" / "still-locked.json").open("wb"):
                self.assertTrue(recover_scratch(index))

        def cleaned():
            return not worker_active(index) and not recover_scratch(index)

        self.wait_for(cleaned, index=index)
        self.assertEqual([path.name for path in root.iterdir()], ["unrelated"])
        self.assertEqual(index.status().phases, {"polish": 1})
        self.assertEqual(index.searchable()[0].raw, "hello world")
        index.path.with_name("hold").write_text("")
        self.assertTrue(request_index(index, settings, lambda: [media]))
        self.wait_state(index, "finished")
        self.assertEqual(index.searchable()[0].polished, "Hello world.")

    def test_about_distinguishes_driver_support_from_the_configured_runtime(self):
        _, settings, _ = self.project("about", "")
        cpu_only = describe_hardware(Hardware("Windows", "CPU", adapters=(), vulkan=False, cuda=False), settings)
        self.assertIn("No GPU detected", cpu_only)
        self.assertIn("Support not detected", cpu_only)
        gpu = describe_hardware(Hardware("Windows", "CPU", adapters=(Adapter("GPU", 8 * 1024**3),),
                                         vulkan=True, cuda=True), settings)
        self.assertIn("8.0 GiB", gpu)
        self.assertRegex(gpu, r"Vulkan\s+Supported\s+Not configured")
        self.assertRegex(gpu, r"CUDA\s+Supported\s+Not configured")
        self.assertEqual(describe_recognition(None, False), "No transcription run yet.")

    def test_about_distinguishes_missing_tools_and_codex_login_status(self):
        _, settings, _ = self.project("dependencies", "")
        missing = str(self.root / "missing.exe")
        absent = replace(settings, ffmpeg=missing, codex=missing, whisper_model=Path(missing))
        report = describe_dependencies(absent, checked=True)
        self.assertRegex(report, r"FFmpeg\s+Missing")
        self.assertRegex(report, r"FFprobe\s+Found")
        self.assertRegex(report, r"Whisper model\s+Missing")
        self.assertRegex(report, r"Codex CLI\s+Missing")
        self.assertIn("Install and sign in to Codex CLI to enable automatic transcript polishing.", report)
        self.assertIn("Raw transcripts are still searchable.", report)

        for code, output, status in (
            (0, "Logged in using ChatGPT", "Found · signed in"),
            (1, "Not logged in", "Found · sign-in required"),
            (1, "Could not read configuration", "Found · sign-in could not be verified"),
        ):
            with self.subTest(status=status), patch("davinci_search.dependencies.run_tool") as probe:
                probe.return_value = subprocess.CompletedProcess([], code, "", output)
                report = describe_dependencies(settings, checked=True)
                self.assertRegex(report, rf"Codex CLI\s+{status}")
                self.assertNotIn(output, report)
