"""Decide which voice speaks which part of a sentence (narrator vs. character dialogue)."""
from __future__ import annotations

from ..formats.model import Book
from ..formats.textproc import has_speech
from .voices import KOKORO_VOICES


class VoicePlanner:
    def __init__(self, book: Book, narrator: str, cast: dict[str, str] | None = None,
                 use_characters: bool = False, pauses: dict[str, float] | None = None):
        self.book = book
        self.narrator = narrator
        self.cast = cast or {}
        self.use_characters = use_characters
        self.pauses = pauses or {"sentence": 0.18, "paragraph": 0.5, "heading": 0.8, "chapter": 1.5}

    def runs(self, u: int) -> list[tuple[str, str]]:
        ci, pi, si = self.book.units[u]
        para = self.book.chapters[ci].paragraphs[pi]
        s = para.sentences[si]
        text = para.text
        if not self.use_characters or not para.quotes:
            return [(self.narrator, text[s.start:s.end])]
        runs: list[list] = []
        pos = s.start
        for q in para.quotes:
            if q.end <= s.start or q.start >= s.end:
                continue
            qs, qe = max(q.start, s.start), min(q.end, s.end)
            if qs > pos:
                runs.append([self.narrator, text[pos:qs]])
            voice = self.cast.get(q.speaker) if q.speaker else None
            runs.append([voice or self.narrator, text[qs:qe]])
            pos = qe
        if pos < s.end:
            runs.append([self.narrator, text[pos:s.end]])
        # glue speechless bits (", " / "." ) to the neighbour, merge same-voice runs
        merged: list[list] = []
        for r in runs:
            if merged and (not has_speech(r[1]) or merged[-1][0] == r[0]):
                merged[-1][1] += r[1]
            else:
                merged.append(r)
        if merged and not has_speech(merged[0][1]) and len(merged) > 1:
            merged[1][1] = merged[0][1] + merged[1][1]
            merged.pop(0)
        return [(v, t) for v, t in merged]

    def voices_used(self) -> set[str]:
        vs = {self.narrator}
        if self.use_characters:
            vs |= set(self.cast.values())
        return vs

    def pause(self, u: int) -> float:
        return self.pauses.get(self.book.pause_after(u), 0.2)


MALE_POOL = [f"kokoro:{v}" for v, _, g, _ in KOKORO_VOICES if g == "male"]
FEMALE_POOL = [f"kokoro:{v}" for v, _, g, _ in KOKORO_VOICES if g == "female"]


def auto_cast(characters: dict[str, dict], narrator: str, existing: dict[str, str] | None = None) -> dict[str, str]:
    """Give every character a voice: by gender, most-talkative first, avoiding the narrator."""
    cast = dict(existing or {})
    used = set(cast.values()) | {narrator}
    pools = {"male": [v for v in MALE_POOL if v not in used],
             "female": [v for v in FEMALE_POOL if v not in used]}
    # how important the busiest character using each voice is (to share voices with minor characters)
    weight: dict[str, int] = {}
    for n, v in cast.items():
        weight[v] = max(weight.get(v, 0), int(characters.get(n, {}).get("lines", 0)))
    turn = 0
    for name, info in sorted(characters.items(), key=lambda kv: -int(kv[1].get("lines", 0))):
        if name in cast:
            continue
        g = info.get("gender", "unknown")
        if g not in pools:
            g = "male" if turn % 2 == 0 else "female"
            turn += 1
        pool = pools[g]
        if pool:
            voice = pool.pop(0)
        else:   # out of voices: share with the least talkative character of that gender
            options = [v for v in (MALE_POOL if g == "male" else FEMALE_POOL) if v != narrator]
            voice = min(options, key=lambda v: weight.get(v, 0))
        cast[name] = voice
        weight[voice] = max(weight.get(voice, 0), int(info.get("lines", 0)))
    return cast
