import unittest

from davinci_search.resolve import source_timecode


class SourceTimecodeTests(unittest.TestCase):
    def test_uses_the_clip_start_and_frame_rate(self):
        props = {"Start TC": "01:00:00:12", "FPS": 24, "Frames": "1440"}

        self.assertEqual(source_timecode(props, 42500), "01:00:43:00")

    def test_skips_drop_frame_numbers_only_at_the_applicable_minute_boundaries(self):
        for rate, start, offset, expected in (
            (29.97, "01:00:59;29", 34, "01:01:00;02"),
            (59.94, "18:53:59;59", 17, "18:54:00;04"),
            (59.94, "18:59:59;59", 17, "19:00:00;00"),
        ):
            with self.subTest(rate=rate, start=start):
                self.assertEqual(source_timecode({"Start TC": start, "FPS": rate}, offset), expected)

    def test_no_timecode_when_position_is_unverified(self):
        for props, offset in (
            ({"Start TC": "01:00:00:00"}, 1000),
            ({"Start TC": "01:00:00:00", "FPS": 0}, 1000),
            ({"Start TC": "01:01:00;00", "FPS": 29.97}, 1000),
            ({"Start TC": "01:00:00:00", "FPS": 24, "Frames": "24"}, 1000),
        ):
            with self.subTest(props=props, offset=offset):
                self.assertIsNone(source_timecode(props, offset))
