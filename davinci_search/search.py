"""Phrase search over timestamped Vietnamese and English transcripts."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import groupby
import re
import unicodedata

from .index import IndexedSegment, Media


WORDS = re.compile(r"[^\W_]+", re.UNICODE)
MAX_QUERY_WORDS = 30
MAX_SEGMENT_GAP_MS = 1500


def words(text: str) -> list[str]:
    return WORDS.findall(unicodedata.normalize("NFC", text).casefold())


@lru_cache(maxsize=16384)
def fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word.replace("đ", "d"))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    before_previous: list[int] = []
    for i, a in enumerate(left, 1):
        row = [i]
        for j, b in enumerate(right, 1):
            cost = min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b))
            if i > 1 and j > 1 and a == right[j - 2] and left[i - 2] == b:
                cost = min(cost, before_previous[j - 2] + 1)
            row.append(cost)
        before_previous, previous = previous, row
    return previous[-1]


@dataclass(frozen=True)
class Hit:
    media: Media
    start_ms: int
    end_ms: int
    excerpt: str
    version: str
    rank: tuple[int, int, int]


def search(rows: list[IndexedSegment], query: str, include_original: bool = False) -> list[Hit]:
    requested = words(query)
    if not requested:
        return []
    if len(requested) > MAX_QUERY_WORDS or any(len(word) > 100 for word in requested):
        raise ValueError("Tìm tối đa 30 từ, mỗi từ tối đa 100 ký tự.")

    folded_query = [fold(word) for word in requested]
    max_edits = max(1, sum(len(word) for word in folded_query) // 8)

    @lru_cache(maxsize=32768)
    def token_cost(index: int, token: str) -> int | None:
        expected = folded_query[index]
        candidate = fold(token)
        if expected == candidate:
            return 0
        limit = 0 if len(expected) < 3 else 1 if len(expected) < 8 else 2
        if not limit or abs(len(candidate) - len(expected)) > limit:
            return None
        edits = edit_distance(expected, candidate)
        return edits if edits <= limit else None

    hits: dict[tuple[str, int], Hit] = {}
    for _, media_rows in groupby(rows, key=lambda row: row.media.key):
        segments = list(media_rows)
        for original in (False, True) if include_original else (False,):
            if original and all(s.polished is None for s in segments):
                continue
            tokens: list[tuple[str, int, int]] = []
            excerpts: list[str] = []
            run = 0
            for index, segment in enumerate(segments):
                if index and segment.start_ms - segments[index - 1].end_ms > MAX_SEGMENT_GAP_MS:
                    run += 1
                value = segment.raw if original or segment.polished is None else segment.polished
                excerpts.append(value)
                segment_words = words(value)
                if not segment_words:
                    run += 1
                tokens.extend((word, index, run) for word in segment_words)

            size = len(requested)
            for start in range(len(tokens) - size + 1):
                first, last = tokens[start], tokens[start + size - 1]
                if first[2] != last[2] or token_cost(0, first[0]) is None:
                    continue
                edits = accents = 0
                for i in range(size):
                    word = tokens[start + i][0]
                    cost = token_cost(i, word)
                    if cost is None or edits + cost > max_edits:
                        break
                    edits += cost
                    accents += word != requested[i]
                else:
                    initial, final = segments[first[1]], segments[last[1]]
                    rank = (edits, accents, int(original))
                    key = (initial.media.key, initial.number)
                    if key not in hits or rank < hits[key].rank:
                        version = (
                            "pending" if initial.polished is None else "raw" if original else "polished"
                        )
                        hits[key] = Hit(
                            initial.media, initial.start_ms, final.end_ms,
                            " ".join(excerpts[first[1]:last[1] + 1]), version, rank,
                        )

    return sorted(hits.values(), key=lambda hit: (hit.rank, hit.media.name.casefold(), hit.start_ms))
