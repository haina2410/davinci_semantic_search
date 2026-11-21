import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from davinci_search.diagnostics import DiagnosticLog, RETENTION_SECONDS, export_bundle, read_events


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        Path(".cache").mkdir(exist_ok=True)

    def test_export_filters_by_time_and_project_without_copying_raw_logs(self):
        with TemporaryDirectory(dir=Path(".cache")) as tmp:
            root = Path(tmp)
            (root / "version.txt").write_text("v1.2.3", encoding="utf-8")
            (root / "resolve-script-error.log").write_text("Private startup traceback", encoding="utf-8")
            (root / "worker.log").write_text("Private transcript", encoding="utf-8")
            startup = DiagnosticLog(root)
            a = DiagnosticLog(root, "project-A", "run-a")
            b = DiagnosticLog(root, "project-B", "run-b")
            now = 2 * RETENTION_SECONDS
            with patch("davinci_search.diagnostics.time", return_value=now - RETENTION_SECONDS - 1):
                a.record("old_error")
            with patch("davinci_search.diagnostics.time", return_value=now - 10):
                startup.record("resolve_connected", version="21.2.0.4")
                a.failure("media_error", "transcription", FileNotFoundError(2, "missing", r"C:\Private\clip.mp4"))
                b.record("index_started")
            with patch("davinci_search.diagnostics.time", return_value=now), patch(
                "davinci_search.diagnostics.gpu_names", return_value=["Fixture GPU"]
            ):
                selected = export_bundle(root, "project-A")
                latest = export_bundle(root, latest=True)
            with ZipFile(selected) as bundle:
                self.assertEqual(set(bundle.namelist()), {"system.json", "events.jsonl"})
                metadata = json.loads(bundle.read("system.json"))
                lines = bundle.read("events.jsonl")
                events = [json.loads(line) for line in lines.splitlines()]
                self.assertEqual(metadata["history_hours"], 72)
                self.assertEqual(metadata["plugin_version"], "v1.2.3")
                self.assertEqual(metadata["resolve_version"], "21.2.0.4")
                self.assertEqual(metadata["gpu_adapters"], ["Fixture GPU"])
                self.assertEqual([event["event"] for event in events], ["resolve_connected", "media_error"])
                self.assertEqual((events[-1]["step"], events[-1]["code"]), ("transcription", 2))
                self.assertNotIn(b"Private", lines)
                self.assertNotIn(b"project-A", lines)
            with ZipFile(latest) as bundle:
                events = [json.loads(line) for line in bundle.read("events.jsonl").splitlines()]
                self.assertEqual([event["event"] for event in events], ["resolve_connected", "index_started"])
                self.assertEqual(events[-1]["run"], "run-b")

    def test_retention_cap_keeps_newest_events(self):
        with TemporaryDirectory(dir=Path(".cache")) as tmp, patch("davinci_search.diagnostics.MAX_EVENTS", 2):
            root = Path(tmp)
            diagnostics = DiagnosticLog(root)
            diagnostics.record("first")
            diagnostics.record("second")
            diagnostics.record("third")
            self.assertEqual([event["event"] for event in read_events(root, "", False)], ["second", "third"])
