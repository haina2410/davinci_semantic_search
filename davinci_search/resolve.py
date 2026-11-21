"""Read the open Resolve project using the in-app scripting API."""

from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import re
from typing import Iterator, Protocol

from .index import Media
from .timeline import Placement, Snapshot, fps, timecode
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


def media_folders(pool):
    stack = [pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        yield folder
        stack.extend(folder.GetSubFolderList() or [])


def media_items(project) -> Iterator[MediaPoolItem]:
    for folder in media_folders(project.GetMediaPool()):
        yield from folder.GetClipList() or []


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


def select_source(project, key: str) -> MediaPoolItem | None:
    pool = project.GetMediaPool()
    for folder in media_folders(pool):
        for item in folder.GetClipList() or []:
            if media_key(item) != key:
                continue
            current = pool.GetCurrentFolder()
            if (not current or current.GetUniqueId() != folder.GetUniqueId()) and not pool.SetCurrentFolder(folder):
                return None
            return item if pool.SetSelectedClip(item) else None
    return None


def source_timecode(properties: dict[str, object], offset_ms: int) -> str | None:
    """Translate an offset from the start of the media to its viewer timecode."""
    start = str(properties.get("Start TC", "")).strip()
    match = re.fullmatch(r"([0-9]{2}):([0-9]{2}):([0-9]{2})([:;])([0-9]{2,3})", start)
    if match is None or offset_ms < 0:
        return None
    try:
        rate = fps(properties.get("FPS"))
    except (IndexError, ValueError, ZeroDivisionError):
        return None
    hours, minutes, seconds, frame = (int(match[group]) for group in (1, 2, 3, 5))
    nominal = round(rate)
    if not (0 <= hours < 24 and minutes < 60 and seconds < 60 and 0 <= frame < nominal <= 120):
        return None
    total_minutes = hours * 60 + minutes
    start_frame = (total_minutes * 60 + seconds) * nominal + frame
    drop = match[4] == ";"
    if drop:
        start_frame -= (nominal // 15) * (total_minutes - total_minutes // 10)
    if start_frame < 0 or timecode(start_frame, rate, drop) != start:
        return None
    offset_frames = math.floor(offset_ms * rate / 1000)
    length = str(properties.get("Frames", ""))
    if length.isdecimal() and offset_frames >= int(length):
        return None
    return timecode(start_frame + offset_frames, rate, drop)


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


def mapped_channels(item) -> int | None:
    try:
        mapping = json.loads(optional_call(item, "GetSourceAudioChannelMapping"))
        count = mapping["embedded_audio_channels"]
        if not isinstance(count, int) or count < 1 or mapping["linked_audio"]:
            return None
        heard = {
            channel for entry in mapping["track_mapping"].values()
            if not entry["mute"] for channel in entry["channel_idx"]
        }
        return count if heard == set(range(1, count + 1)) else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def placement(item, source, path: str, track: int, rate: Fraction, audit: TimingAudit) -> Placement | None:
    """Accept only forward, real-time source ranges corroborated by the clip edit points."""
    channels = mapped_channels(item)
    if channels is None:
        return None
    speed = optional_call(item, "GetSpeed")
    speed = {str(key).casefold(): value for key, value in speed.items()} if isinstance(speed, dict) else {}
    try:
        if number(speed.get("percentage", 100)) != 100 or any(
            str(speed.get(key)).casefold() in ("1", "true", "yes")
            for key in ("reverse", "reversespeed", "freeze", "freezeframe", "variable")
        ):
            return None
        record_start, record_end = (number(item.GetStart(True)), number(item.GetEnd(True)))
        source_start = number(item.GetSourceStartTime())
        source_end = number(item.GetSourceEndTime())
        record_length = (record_end - record_start) / rate
        tolerance = 1 / (20 * rate)
        if record_length <= 0 or abs(source_end - source_start - record_length) > tolerance:
            return None
        offset = audit.source_offset(path, record_start / rate, record_length, source_start, tolerance)
        if offset is None or offset < 0:
            return None
    except (AttributeError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    return Placement(item.GetUniqueId(), media_key(source), path, channels, track, record_start,
                     record_end, offset * 1000, (offset + record_length) * 1000)


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
            path = canonical_path(file_path)
            verified = placement(item, source, path, track, rate, audit)
            if verified:
                placements.append(verified)
            else:
                unverified.add(key)
    return Snapshot(timeline.GetUniqueId(), int(timeline.GetStartFrame()), int(timeline.GetEndFrame()),
                    rate, drop, tuple(placements), frozenset(unverified), opaque)
