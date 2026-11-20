"""Read the open Resolve project using the in-app scripting API."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Iterator, Protocol

from .index import Media
from .timeline import Placement, Snapshot, fps
from .timeline_audit import TimingAudit, canonical_path


COMPLEX_TYPES = ("compound", "multicam", "fusion", "timeline", "synced", "nested")
NON_MEDIA_TYPES = ("still", "image", "generator", "title", "adjustment", "subtitle")


class MediaPoolItem(Protocol):
    def GetUniqueId(self) -> str: ...
    def GetMediaId(self) -> str: ...
    def GetName(self) -> str: ...
    def GetClipProperty(self, key: str | None = None) -> str | dict[str, object]: ...


def media_key(item: MediaPoolItem) -> str:
    return str(item.GetUniqueId() or item.GetMediaId() or "")


def media_items(project) -> Iterator[MediaPoolItem]:
    stack = [project.GetMediaPool().GetRootFolder()]
    while stack:
        folder = stack.pop()
        yield from folder.GetClipList() or []
        stack.extend(folder.GetSubFolderList() or [])


def scan_media(project) -> list[Media]:
    found: dict[str, Media] = {}
    for item in media_items(project):
        props = item.GetClipProperty()
        if not isinstance(props, dict):
            continue
        file_path = props.get("File Path")
        kind = str(props.get("Type") or "").casefold()
        key = media_key(item)
        if not key or not isinstance(file_path, str) or not file_path or any(
            label in kind for label in COMPLEX_TYPES + NON_MEDIA_TYPES
        ):
            continue
        path = Path(file_path)
        if not path.is_absolute():
            continue
        found[key] = Media.from_path(key, item.GetName(), str(path))
    return list(found.values())


def select_source(project, key: str) -> bool:
    return any(
        bool(project.GetMediaPool().SetSelectedClip(item))
        for item in media_items(project) if media_key(item) == key
    )


def optional_call(obj, method: str, *args):
    function = getattr(obj, method, None)
    return function(*args) if callable(function) else None


def timeline_setting(timeline, name: str) -> object:
    value = optional_call(timeline, "GetSetting", name)
    if value is None or value == "":
        settings = optional_call(timeline, "GetSettings")
        if isinstance(settings, dict):
            return settings.get(name)
    return value


def number(value: object) -> Fraction:
    return Fraction(str(value))


def placement(item, source, track: int, rate: Fraction) -> Placement | None:
    """Accept only forward, real-time source ranges corroborated by the clip edit points."""
    speed = optional_call(item, "GetSpeed")
    speed = {str(key).casefold(): value for key, value in speed.items()} if isinstance(speed, dict) else {}
    try:
        if number(speed.get("percentage", 100)) != 100 or any(
            str(speed.get(key)).casefold() in ("1", "true", "yes")
            for key in ("reverse", "reversespeed", "freeze", "freezeframe", "variable")
        ):
            return None
        record_start, record_end = (number(item.GetStart(True)), number(item.GetEnd(True)))
        trim_ms = number(item.GetLeftOffset(True)) * 1000 / rate
        record_length = (record_end - record_start) * 1000 / rate
        source_fps = source.GetClipProperty("FPS")
        source_rate = fps(source_fps) if source_fps and str(source_fps) != "0" else rate
        first, last = number(item.GetSourceStartFrame()), number(item.GetSourceEndFrame())
        start_ms, end_ms = first * 1000 / source_rate, last * 1000 / source_rate
        tolerance = max(1000 / source_rate, 1000 / rate)
        if first < 0 or last < first or record_length <= 0 or abs(start_ms - trim_ms) > tolerance:
            return None
        span = end_ms - start_ms
        # Some clip types describe the source end by its last frame, others by its boundary.
        if abs(span - record_length) <= tolerance / 2:
            boundary = end_ms
        elif abs(span + 1000 / source_rate - record_length) <= tolerance / 2:
            boundary = end_ms + 1000 / source_rate
        else:
            return None
    except (AttributeError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    return Placement(item.GetUniqueId(), media_key(source), track, record_start,
                     record_end, start_ms, boundary)


def read_timeline(timeline, audit: TimingAudit) -> Snapshot:
    setting = timeline_setting(timeline, "timelineFrameRate")
    rate = fps(setting)
    if rate <= 0:
        raise ValueError("Timeline có tốc độ khung hình không hợp lệ.")
    drop = "DF" in str(setting) or str(
        timeline_setting(timeline, "timelineDropFrameTimecode")
    ).lower() in ("1", "true", "yes")
    placements: list[Placement] = []
    unverified: set[str] = set()
    opaque = audit.opaque
    for track in range(1, timeline.GetTrackCount("audio") + 1):
        if timeline.GetIsTrackEnabled("audio", track) is False:
            continue
        for item in timeline.GetItemListInTrack("audio", track) or []:
            if optional_call(item, "GetClipEnabled") is False:
                continue
            source = item.GetMediaPoolItem()
            if not source:
                opaque = True
                continue
            key = media_key(source)
            file_path = source.GetClipProperty("File Path")
            kind = str(source.GetClipProperty("Type") or "").casefold()
            if not file_path or not key or any(label in kind for label in COMPLEX_TYPES):
                opaque = True
                continue
            verified = None
            if canonical_path(file_path) in audit.verified_paths:
                verified = placement(item, source, track, rate)
            if verified:
                placements.append(verified)
            else:
                unverified.add(key)
    return Snapshot(timeline.GetUniqueId(), int(timeline.GetStartFrame()), int(timeline.GetEndFrame()),
                    rate, drop, tuple(placements), frozenset(unverified), opaque)
