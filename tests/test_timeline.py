from dataclasses import replace
from fractions import Fraction
import unittest
from xml.etree import ElementTree

from davinci_search.index import Media
from davinci_search.search import Hit
from davinci_search.timeline import (
    MARKER_PREFIX, Placement, Snapshot, add_search_marker, clear_search_markers, project_hits, timecode,
)
from davinci_search.timeline_audit import parse_audit


class ResolveMarkerApi:
    def __init__(self):
        self.markers = {50: {"customData": "editor", "name": "Nhịp cắt"}}

    def GetUniqueId(self):
        return "timeline"

    def GetMarkers(self):
        return dict(self.markers)

    def AddMarker(self, frame, color, name, note, duration, custom_data):
        if frame in self.markers:
            return False
        self.markers[frame] = {"customData": custom_data, "name": name}
        return True

    def DeleteMarkerByCustomData(self, tag):
        frame = next(frame for frame, marker in self.markers.items() if marker["customData"] == tag)
        del self.markers[frame]
        return True


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.media = Media("source", "A", r"D:\media\A.mov", "revision", 1)
        self.hit = Hit(self.media, 34000, 35000, "Đây là câu thoại", "polished", (0, 0, 0))
        self.first = Placement("first", "source", "d:/media/a.mov", 1, 1, Fraction(86400), Fraction(86640),
                               Fraction(30000), Fraction(40000))
        self.second = replace(self.first, key="second", track=2, record_start=Fraction(86800),
                              record_end=Fraction(87040))
        self.snapshot = Snapshot("timeline", 86400, 88700, Fraction(24), False,
                                 (self.first, self.second), frozenset(), False)

    def test_repeated_audio_placements_have_distinct_timeline_frames(self):
        outside = replace(self.hit, start_ms=5000, end_ms=7000)
        straddles = replace(self.hit, start_ms=29500, end_ms=30500)
        results = project_hits(self.snapshot, [self.hit, outside, straddles])
        self.assertEqual([result.frame for result in results.inside], [86496, 86896])
        self.assertEqual(results.outside, [outside])
        self.assertEqual(results.unverified, [straddles])

    def test_a_changed_link_or_audio_mapping_cannot_reuse_the_old_transcript(self):
        for placement in (replace(self.first, source_path="d:/media/relinked.mov"),
                          replace(self.first, audio_channels=2)):
            with self.subTest(placement=placement):
                changed = replace(self.snapshot, placements=(placement,))
                result = project_hits(changed, [self.hit])
                self.assertEqual(result.inside, [])
                self.assertEqual(result.unverified, [self.hit])

    def test_new_search_cleans_only_owned_markers_and_recalculates_after_move(self):
        timeline = ResolveMarkerApi()
        result = project_hits(self.snapshot, [self.hit]).inside[0]
        self.assertEqual(add_search_marker(timeline, self.snapshot, result, "search-1"), "added")
        self.assertTrue(timeline.markers[96]["customData"].startswith(MARKER_PREFIX))
        self.assertEqual(add_search_marker(timeline, self.snapshot, result, "search-1"), "exists")
        self.assertEqual(add_search_marker(timeline, self.snapshot, replace(result, frame=86450), "search-1"),
                         "occupied")
        self.assertEqual(clear_search_markers(timeline), 0)
        moved = replace(self.first, record_start=Fraction(86424), record_end=Fraction(86664))
        new = replace(self.snapshot, placements=(moved,))
        updated = project_hits(new, [self.hit]).inside[0]
        self.assertEqual(add_search_marker(timeline, new, updated, "search-2"), "added")
        self.assertEqual(set(timeline.markers), {50, 120})

    def test_retime_in_export_makes_its_source_unverified(self):
        root = ElementTree.fromstring("""<fcpxml><resources>
          <asset id="a" start="3600s"><media-rep src="file://localhost/D:/media/A.mov" /></asset>
          <asset id="b" src="file:///D:/media/B%20C.wav" />
        </resources><project><sequence><spine>
          <asset-clip ref="a" offset="10s" start="3605s" duration="4s">
            <timeMap><timept time="0s" value="0s"/></timeMap>
          </asset-clip>
          <asset-clip ref="a" offset="20s" start="3605s" duration="4s"/>
          <gap offset="24s" start="12s"><audio ref="b" offset="13s" start="1s" duration="3s"/></gap>
        </spine></sequence></project></fcpxml>""")
        audit = parse_audit(root)
        tolerance = Fraction(1, 480)
        self.assertIsNone(audit.source_offset("d:/media/a.mov", Fraction(10), Fraction(4), Fraction(3605), tolerance))
        self.assertEqual(audit.source_offset("d:/media/a.mov", Fraction(20), Fraction(4), Fraction(3605), tolerance), 5)
        self.assertEqual(audit.source_offset("d:/media/b c.wav", Fraction(25), Fraction(3), Fraction(1), tolerance), 1)
        unverified = replace(self.snapshot, placements=(), unverified=frozenset({"source"}))
        results = project_hits(unverified, [self.hit])
        self.assertEqual(results.unverified, [self.hit])
        self.assertEqual(results.outside, [])

    def test_drop_frame_display_skips_two_labels_at_one_minute(self):
        self.assertEqual(timecode(1800, Fraction(30000, 1001), True), "00:01:00;02")
        self.assertEqual(timecode(17982, Fraction(30000, 1001), True), "00:10:00;00")


if __name__ == "__main__":
    unittest.main()
