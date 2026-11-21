"""Map transcript hits to timeline placements and manage this tool's markers."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import re
from uuid import uuid4

from .search import Hit
from .timeline_audit import canonical_path


MARKER_PREFIX = "davinci-semantic-search/v1/"
NTSC = {"23.976": Fraction(24000, 1001), "23.98": Fraction(24000, 1001),
        "29.97": Fraction(30000, 1001), "47.952": Fraction(48000, 1001),
        "59.94": Fraction(60000, 1001), "119.88": Fraction(120000, 1001)}


def fps(value: object) -> Fraction:
    rate = str(value).split()[0]
    return NTSC[rate] if rate in NTSC else Fraction(rate)


def timecode(frame: int, rate: Fraction, drop_frame: bool = False) -> str:
    nominal = round(rate)
    separator = ":"
    if drop_frame and rate in (NTSC["29.97"], NTSC["59.94"], NTSC["119.88"]):
        separator = ";"
        dropped = nominal // 15
        frames_per_ten_minutes = nominal * 600 - dropped * 9
        frames_per_minute = nominal * 60 - dropped
        ten_minutes, remaining = divmod(frame, frames_per_ten_minutes)
        frame += dropped * (ten_minutes * 9 + max(0, remaining - dropped) // frames_per_minute)
    secs, frames = divmod(frame, nominal)
    mins, seconds = divmod(secs, 60)
    hours, minutes = divmod(mins, 60)
    return f"{hours % 24:02d}:{minutes:02d}:{seconds:02d}{separator}{frames:02d}"


@dataclass(frozen=True)
class Placement:
    key: str
    source_key: str
    source_path: str
    audio_channels: int
    track: int
    record_start: Fraction
    record_end: Fraction
    source_start_ms: Fraction
    source_end_ms: Fraction


@dataclass(frozen=True)
class Snapshot:
    key: str
    start_frame: int
    end_frame: int
    frame_rate: Fraction
    drop_frame: bool
    placements: tuple[Placement, ...]
    unverified: frozenset[str]
    opaque: bool


@dataclass(frozen=True)
class TimelineHit:
    hit: Hit
    placement: Placement
    frame: int


@dataclass(frozen=True)
class TimelineResults:
    inside: list[TimelineHit]
    outside: list[Hit]
    unverified: list[Hit]


def project_hits(snapshot: Snapshot, hits: list[Hit]) -> TimelineResults:
    by_source: dict[str, list[Placement]] = {}
    for placement in snapshot.placements:
        by_source.setdefault(placement.source_key, []).append(placement)
    results = TimelineResults([], [], [])
    for hit in hits:
        unknown = snapshot.opaque or hit.media.key in snapshot.unverified
        found = False
        for placement in by_source.get(hit.media.key, []):
            if (placement.source_path != canonical_path(hit.media.path)
                    or placement.audio_channels != hit.media.audio_channels):
                unknown = True
                continue
            if hit.end_ms <= placement.source_start_ms or hit.start_ms >= placement.source_end_ms:
                continue
            if hit.start_ms < placement.source_start_ms or hit.end_ms > placement.source_end_ms:
                unknown = True
                continue
            offset = (hit.start_ms - placement.source_start_ms) * snapshot.frame_rate / 1000
            frame = math.floor(placement.record_start + offset)
            if frame < math.ceil(placement.record_start) or frame >= placement.record_end:
                unknown = True
                continue
            found = True
            results.inside.append(TimelineHit(hit, placement, frame))
        if not found:
            target = results.unverified if unknown else results.outside
            target.append(hit)
    results.inside.sort(key=lambda item: (item.hit.rank, item.frame, item.placement.track))
    return results


def is_search_marker(marker: dict) -> bool:
    tag = marker.get("customData")
    return isinstance(tag, str) and tag.startswith(MARKER_PREFIX)


def clear_search_markers(timeline) -> tuple[int, int]:
    removed = failed = 0
    for marker in (timeline.GetMarkers() or {}).values():
        if not is_search_marker(marker):
            continue
        if timeline.DeleteMarkerByCustomData(marker["customData"]):
            removed += 1
        else:
            failed += 1
    return removed, failed


def mark_search_result(timeline, snapshot: Snapshot, result: TimelineHit) -> tuple[str, bool]:
    """Mark the result on the timeline ruler and seek to its absolute timecode."""
    if timeline.GetUniqueId() != snapshot.key or not snapshot.start_frame <= result.frame < snapshot.end_frame:
        return "stale", False
    relative = result.frame - snapshot.start_frame
    existing = (timeline.GetMarkers() or {}).get(relative)
    if existing:
        state = "exists" if is_search_marker(existing) else "occupied"
    else:
        name = f"Lời thoại · {result.hit.media.name}"[:70]
        text = re.sub(r"\s+", " ", result.hit.excerpt).strip()
        if not timeline.AddMarker(relative, "Cyan", name, text[:1000], 1, f"{MARKER_PREFIX}{uuid4().hex}"):
            return "failed", False
        state = "added"
    target = timecode(result.frame, snapshot.frame_rate, snapshot.drop_frame)
    return state, bool(timeline.SetCurrentTimecode(target))
