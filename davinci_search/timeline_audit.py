"""Use Resolve's FCPXML export to exclude unsupported timing transforms."""

from __future__ import annotations

from dataclasses import dataclass
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
class TimingAudit:
    verified_paths: frozenset[str]
    opaque: bool


def parse_audit(document: xml.Element) -> TimingAudit:
    resources = document.find("resources")
    if document.tag != "fcpxml" or resources is None:
        return TimingAudit(frozenset(), True)
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

    known: set[str] = set()
    blocked: set[str] = set()
    opaque = False
    for sequence in document.findall(".//project/sequence"):
        for element in sequence.iter():
            if element.tag in COMPLEX_ELEMENTS or element.find("timeMap") is not None:
                affected = paths(element)
                blocked.update(affected)
                if not affected:
                    opaque = True
            elif element.tag in ASSET_ELEMENTS and element.get("ref") in by_id:
                known.update(paths(element))
    return TimingAudit(frozenset(known - blocked), opaque)


def audit_timeline(resolve, timeline, scratch: Path) -> TimingAudit:
    if timeline.GetTrackCount("audio") == 0:
        return TimingAudit(frozenset(), False)
    try:
        with TemporaryDirectory(prefix="timeline-", dir=scratch) as directory:
            exported = Path(directory) / "audit.fcpxml"
            export_type = resolve.EXPORT_FCPXML_1_10
            if not timeline.Export(str(exported), export_type, resolve.EXPORT_NONE):
                return TimingAudit(frozenset(), True)
            if exported.is_dir():
                exported = exported / "Info.fcpxml"
            return parse_audit(xml.parse(exported).getroot())
    except (AttributeError, OSError, TypeError, ValueError, xml.ParseError):
        return TimingAudit(frozenset(), True)
