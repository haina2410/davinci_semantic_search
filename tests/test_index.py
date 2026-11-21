import tempfile
import unittest
from pathlib import Path

from davinci_search.diagnostics import DiagnosticLog, read_events
from davinci_search.index import Index, Media, Segment, Transcript
from davinci_search.polish import PolishError
from davinci_search.search import search
from davinci_search.worker import claim_worker, run_queue, worker_active


class IndexTests(unittest.TestCase):
    def setUp(self):
        cache = Path(".cache")
        cache.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db_path = root / "index.sqlite3"
        self.index = Index(self.db_path)
        self.source = root / "source.wav"
        self.source.write_bytes(b"fixture")
        self.media = Media.from_path("source", "Lời thoại", str(self.source))
        self.index.queue([self.media])

    def test_reopen_after_interrupt_resumes_at_polish(self):
        def interrupted(_):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            run_queue(self.index, lambda _: Transcript([Segment(4000, 9000, "Đi đến cửa hàng")], 1), interrupted)

        reopened = Index(self.db_path)
        self.assertTrue(reopened.status().requested)
        result = search(reopened.searchable(), "di den cua hang")[0]
        self.assertEqual((result.version, result.start_ms), ("pending", 4000))
        reopened.queue([self.media])
        run_queue(reopened, lambda _: self.fail("ASR already ran"), lambda raw: {0: "Đi đến cửa hàng."})
        result = search(reopened.searchable(), "đi đến cửa hnag")[0]
        self.assertEqual((result.version, result.start_ms), ("polished", 4000))
        self.assertFalse(reopened.status().needs_attention)

    def test_edited_source_is_recognized_again(self):
        run_queue(self.index, lambda _: Transcript([Segment(1, 1000, "The original recording")], 1), lambda raw: raw)
        self.source.write_bytes(b"edited fixture")
        new_media = Media.from_path(self.media.key, self.media.name, str(self.source))
        self.index.queue([new_media])
        run_queue(self.index, lambda _: Transcript([Segment(5, 500, "A replacement")], 1), lambda raw: raw)
        self.assertFalse(search(self.index.searchable(), "original"))
        self.assertEqual(search(self.index.searchable(), "replacement")[0].start_ms, 5)

    def test_codex_failure_keeps_raw_and_allows_retry(self):
        def unavailable(_):
            raise PolishError("Codex unavailable")

        diagnostics = DiagnosticLog(self.db_path.parent)
        run_queue(self.index, lambda _: Transcript([Segment(1, 100, "He found the door")], 1), unavailable, diagnostics)
        self.assertTrue(self.index.status().needs_attention)
        self.assertEqual(search(self.index.searchable(), "found the door")[0].version, "pending")
        self.index.queue([self.media])
        run_queue(self.index, lambda _: self.fail("ASR already ran"), lambda _: {0: "He opened the door"})
        self.assertFalse(search(self.index.searchable(), "found the door"))
        self.assertEqual(search(self.index.searchable(), "teh door")[0].version, "polished")
        self.assertEqual(search(self.index.searchable(), "found the door", include_original=True)[0].version, "raw")
        self.assertFalse(self.index.status().errors)
        events = read_events(self.db_path.parent, "", False)
        self.assertEqual([(event["event"], event["step"], event["error"]) for event in events],
                         [("media_error", "codex", "PolishError")])

    def test_only_one_worker_can_claim_project(self):
        with claim_worker(self.index.path) as first:
            self.assertTrue(first)
            self.assertTrue(worker_active(self.index))
        self.assertFalse(worker_active(self.index))

    def test_enabling_vad_requeues_cached_transcript_and_replaces_it_on_success(self):
        run_queue(self.index, lambda _: Transcript([Segment(1, 100, "Old transcript")], 1), lambda raw: raw)

        reopened = Index(self.db_path)
        reopened.queue([self.media], vad_enabled=True)
        self.assertEqual(reopened.status().phases, {"asr": 1})
        self.assertEqual(reopened.raw(self.media), {0: "Old transcript"})

        run_queue(reopened, lambda _: Transcript([], 1, vad_enabled=True), lambda _: self.fail("No speech"))
        self.assertFalse(reopened.searchable())
        reopened.queue([self.media], vad_enabled=True)
        self.assertEqual(reopened.status().phases, {"silent": 1})


if __name__ == "__main__":
    unittest.main()
