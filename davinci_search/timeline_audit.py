"""Use Resolve's FCPXML export to exclude unsupported timing transforms."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as xml


COMPLEX_ELEMENTS = {"ref-clip", "mc-clip", "sync-clip", "audition"}
ASSET_ELEMENTS = {"asset-clip", "clip", "audio", "video"}


def canonical_path(value: str) -> str:
    value = value.replace("\\", "/")
    if value.startswith("file:"):
        url = urlsplit(value)
        host = "" if url.netloc.lower() in ("", "localhost") else "//" + url.netloc
        value = host + unquote(url.path)
    if re.match(r"^/[A-Za-z]:/", value):
        value = value[1:]
    return value.casefold()


@dataclass(frozen=True)
class ExportedRange:
    start: Fraction
    duration: Fraction
    source_start: Fraction
    media_start: Fraction


@dataclass(frozen=True)
class TimingAudit:
    ranges: dict[str, list[ExportedRange]]
    blocked: dict[str, list[Fraction | None]]
    opaque: bool

    def source_offset(
        self, path: str, first: Fraction, length: Fraction, source_start: Fraction, tolerance: Fraction,
    ) -> Fraction | None:
        if any(start is None or abs(start - first) <= tolerance for start in self.blocked.get(path, [])):
            return None
        offsets = {
            edit.source_start - edit.media_start for edit in self.ranges.get(path, [])
            if abs(edit.start - first) <= tolerance and abs(edit.duration - length) <= tolerance
            and abs(edit.source_start - source_start) <= tolerance
        }
        return offsets.pop() if len(offsets) == 1 else None


def xml_time(value: str | None) -> Fraction | None:
    if value and value.endswith("s"):
        return Fraction(value[:-1])
    return None


def parse_audit(document: xml.Element) -> TimingAudit:
    resources = document.find("resources")
    if document.tag != "fcpxml" or resources is None:
        return TimingAudit({}, {}, True)
    by_id = {key: element for element in resources if (key := element.get("id"))}

    def paths(element: xml.Element, visited: frozenset[str] = frozenset()) -> set[str]:
        if element.tag == "asset":
            return {
                canonical_path(source) for child in (element, *element.findall("media-rep"))
                if (source := child.get("src"))
            }
        result: set[str] = set()
        ref = element.get("ref")
        if ref is not None and ref in by_id and ref not in visited:
            result.update(paths(by_id[ref], visited | {ref}))
        for child in element:
            result.update(paths(child, visited))
        return result

    ranges: dict[str, list[ExportedRange]] = {}
    blocked: dict[str, list[Fraction | None]] = {}
    opaque = False

    def visit(element: xml.Element, shift: Fraction) -> None:
        nonlocal opaque
        offset = xml_time(element.get("offset"))
        absolute = shift + offset if offset is not None else None
        has_transform = any(element.find(tag) is not None for tag in ("timeMap", "conform-rate"))
        if element.tag in COMPLEX_ELEMENTS or has_transform:
            affected = paths(element)
            for path in affected:
                blocked.setdefault(path, []).append(absolute)
            if not affected:
                opaque = True
            return

        duration = xml_time(element.get("duration"))
        source_start = xml_time(element.get("start"))
        ref = element.get("ref")
        if (absolute is not None and duration is not None and element.tag in ASSET_ELEMENTS
                and source_start is not None and ref in by_id and by_id[ref].tag == "asset"):
            asset = by_id[ref]
            media_start = xml_time(asset.get("start")) or Fraction(0)
            edit = ExportedRange(absolute, duration, source_start, media_start)
            for path in paths(asset):
                ranges.setdefault(path, []).append(edit)

        if absolute is not None:
            shift = absolute - (source_start or Fraction(0))
        for child in element:
            visit(child, shift)

    for sequence in document.findall(".//project/sequence"):
        visit(sequence, Fraction(0))
    return TimingAudit(ranges, blocked, opaque)


def audit_timeline(resolve, timeline, scratch: Path) -> TimingAudit:
    if timeline.GetTrackCount("audio") == 0:
        return TimingAudit({}, {}, False)
    try:
        with TemporaryDirectory(prefix="timeline-", dir=scratch) as directory:
            exported = Path(directory) / "audit.fcpxml"
            export_type = resolve.EXPORT_FCPXML_1_10
            if not timeline.Export(str(exported), export_type, resolve.EXPORT_NONE):
                return TimingAudit({}, {}, True)
            if exported.is_dir():
                exported = exported / "Info.fcpxml"
            return parse_audit(xml.parse(exported).getroot())
    except (AttributeError, OSError, TypeError, ValueError, ZeroDivisionError, xml.ParseError):
        return TimingAudit({}, {}, True)
