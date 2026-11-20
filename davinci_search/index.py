"""Durable per-project transcripts and the index queue."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import hashlib
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class Media:
    key: str
    name: str
    path: str
    revision: str | None
    audio_channels: int | None = None

    @classmethod
    def from_path(cls, key: str, name: str, path: str) -> Media:
        try:
            file = Path(path)
            stat = file.stat()
            revision = f"{stat.st_size}:{stat.st_mtime_ns}" if file.is_file() else None
        except OSError:
            revision = None
        return cls(key, name, path, revision)


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Transcript:
    segments: list[Segment]
    audio_channels: int


@dataclass(frozen=True)
class IndexedSegment:
    media: Media
    number: int
    start_ms: int
    end_ms: int
    raw: str
    polished: str | None


@dataclass(frozen=True)
class IndexStatus:
    requested: bool
    phases: dict[str, int]
    errors: tuple[tuple[str, str], ...]

    @property
    def needs_attention(self) -> bool:
        return self.requested or bool(self.errors)


def project_db(directory: Path, project_id: str) -> Path:
    if not project_id:
        raise ValueError("Resolve không cung cấp ID của project.")
    filename = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return directory / "projects" / filename / "index.sqlite3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
    revision TEXT, present INTEGER NOT NULL, available INTEGER NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('asr', 'polish', 'done', 'silent')),
    error TEXT, audio_channels INTEGER
);
CREATE TABLE IF NOT EXISTS segments (
    media_key TEXT NOT NULL REFERENCES media(key) ON DELETE CASCADE,
    number INTEGER NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    raw TEXT NOT NULL, polished TEXT,
    PRIMARY KEY (media_key, number)
);
CREATE TABLE IF NOT EXISTS request (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pending INTEGER NOT NULL
);
INSERT OR IGNORE INTO request VALUES (1, 0);
"""


class Index:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            with db:
                db.execute("BEGIN IMMEDIATE")
                if "audio_channels" not in {row[1] for row in db.execute("PRAGMA table_info(media)")}:
                    db.execute("ALTER TABLE media ADD COLUMN audio_channels INTEGER")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def queue(self, media: Iterable[Media]) -> None:
        """Replace the project inventory and request a run in one transaction."""
        with closing(self._connect()) as db, db:
            db.execute("UPDATE media SET present=0")
            for item in media:
                old = db.execute(
                    "SELECT path, revision, phase, audio_channels FROM media WHERE key=?", (item.key,)
                ).fetchone()
                invalidated = old is None or old["path"] != item.path or (
                    item.revision is not None and (
                        old["revision"] != item.revision
                        or (old["phase"] in ("polish", "done") and old["audio_channels"] is None)
                    )
                )
                if invalidated:
                    db.execute("DELETE FROM media WHERE key=?", (item.key,))
                    db.execute(
                        "INSERT INTO media VALUES (?, ?, ?, ?, 1, ?, 'asr', NULL, NULL)",
                        (item.key, item.name, item.path, item.revision, item.revision is not None),
                    )
                else:
                    db.execute(
                        "UPDATE media SET present=1, available=?, name=?, error=NULL WHERE key=?",
                        (item.revision is not None, item.name, item.key),
                    )
            db.execute("UPDATE request SET pending=1 WHERE id=1")

    def pending(self, phase: str) -> list[Media]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT key, name, path, revision FROM media
                   WHERE present=1 AND available=1 AND phase=? ORDER BY name, key""",
                (phase,),
            )
            return [Media(*row) for row in rows]

    def save_transcript(self, media: Media, transcript: Transcript) -> None:
        segments = transcript.segments
        with closing(self._connect()) as db, db:
            changed = db.execute(
                """UPDATE media SET phase=?, error=NULL, audio_channels=?
                   WHERE key=? AND revision=? AND phase='asr' AND present=1""",
                ("polish" if segments else "silent", transcript.audio_channels, media.key, media.revision),
            )
            if changed.rowcount:
                db.executemany(
                    "INSERT INTO segments VALUES (?, ?, ?, ?, ?, NULL)",
                    ((media.key, i, s.start_ms, s.end_ms, s.text) for i, s in enumerate(segments)),
                )

    def raw(self, media: Media) -> dict[int, str]:
        with closing(self._connect()) as db:
            return dict(db.execute(
                "SELECT number, raw FROM segments WHERE media_key=? ORDER BY number", (media.key,)
            ).fetchall())

    def save_polish(self, media: Media, edits: dict[int, str]) -> None:
        with closing(self._connect()) as db, db:
            changed = db.execute(
                """UPDATE media SET phase='done', error=NULL
                   WHERE key=? AND revision=? AND phase='polish' AND present=1""",
                (media.key, media.revision),
            )
            if changed.rowcount:
                numbers = {row[0] for row in db.execute(
                    "SELECT number FROM segments WHERE media_key=?", (media.key,)
                )}
                if numbers != edits.keys():
                    raise ValueError("Mã đoạn hiệu đính không khớp transcript gốc.")
                db.executemany(
                    "UPDATE segments SET polished=? WHERE media_key=? AND number=?",
                    ((edited, media.key, number) for number, edited in edits.items()),
                )

    def record_error(self, media: Media, message: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE media SET error=? WHERE key=? AND revision=?",
                (message, media.key, media.revision),
            )

    def finish(self) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE request SET pending=0 WHERE id=1")

    def status(self) -> IndexStatus:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            requested = bool(db.execute("SELECT pending FROM request WHERE id=1").fetchone()[0])
            phases = dict(db.execute(
                """SELECT CASE WHEN available=0 THEN 'offline' ELSE phase END, COUNT(*)
                   FROM media WHERE present=1 GROUP BY 1"""
            ).fetchall())
            errors = tuple(map(tuple, db.execute(
                "SELECT name, error FROM media WHERE present=1 AND error IS NOT NULL ORDER BY name"
            )))
        return IndexStatus(requested, phases, errors)

    def searchable(self) -> list[IndexedSegment]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT key, name, path, revision, audio_channels, number, start_ms, end_ms, raw, polished
                   FROM media JOIN segments ON media.key=segments.media_key
                   WHERE present=1 ORDER BY media.key, number"""
            )
            return [IndexedSegment(Media(*row[:5]), *row[5:]) for row in rows]
