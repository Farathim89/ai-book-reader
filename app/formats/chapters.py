"""Fix books whose table of contents is missing or useless: split very long chapters at
chapter headings found in the text, or into ~5000-word parts when there are none."""
from __future__ import annotations

import re

from .model import Chapter, Paragraph
from .plaintext import CHAPTER_RE

NUMBERED_RE = re.compile(r"^(\d{1,3}|[IVXLC]{1,7})[.:)]?\s+[A-Z‘“\"'][^.!?]{1,60}$")
LONG_CHAPTER_WORDS = 15000
PART_WORDS = 5000


def _words(paras: list[Paragraph]) -> int:
    return sum(p.text.count(" ") + 1 for p in paras)


def _is_heading(p: Paragraph) -> bool:
    t = p.text.strip()
    if len(t) > 70 or "...." in t or "……" in t:   # contents lines with dot leaders
        return False
    return p.kind == "h" or bool(CHAPTER_RE.match(t)) or bool(NUMBERED_RE.match(t))


def _clean_title(t: str) -> str:
    t = re.sub(r"[.…\s]{3,}\d*\s*$", "", t)   # dot leaders / page numbers
    return t.strip(" .-–—")[:70] or "Chapter"


def refine_chapters(chapters: list[Chapter]) -> list[Chapter]:
    out = _refine(chapters)
    for c in out:
        c.title = _clean_title(c.title)
    return out


def _refine(chapters: list[Chapter]) -> list[Chapter]:
    out: list[Chapter] = []
    for ch in chapters:
        if _words(ch.paragraphs) < LONG_CHAPTER_WORDS:
            out.append(ch)
            continue
        idx = [i for i, p in enumerate(ch.paragraphs) if _is_heading(p)]
        parts: list[Chapter] = []
        if len(idx) >= 3:
            if idx[0] > 0:
                parts.append(Chapter(ch.title, ch.paragraphs[:idx[0]]))
            for k, i in enumerate(idx):
                end = idx[k + 1] if k + 1 < len(idx) else len(ch.paragraphs)
                ch.paragraphs[i].kind = "h"
                parts.append(Chapter(_clean_title(ch.paragraphs[i].text), ch.paragraphs[i:end]))
            # headings right after each other ("PART 1" + "CHAPTER 1", or a contents list) -> merge forward
            merged: list[Chapter] = []
            carry: list[Paragraph] = []
            carry_title = ""
            for c in parts:
                if len(c.paragraphs) < 4 and c is not parts[-1]:
                    carry += c.paragraphs
                    carry_title = carry_title or c.title
                    continue
                if carry:
                    title = c.title if len(carry) > 3 else f"{_clean_title(carry_title)} – {c.title}"
                    c = Chapter(title, carry + c.paragraphs)
                    carry, carry_title = [], ""
                merged.append(c)
            parts = merged
        if len(parts) < 2:   # no usable headings: fixed-size parts
            parts, cur, words = [], None, 0
            for p in ch.paragraphs:
                if cur is None or words >= PART_WORDS:
                    cur = Chapter(f"{ch.title} – part {len(parts) + 1}" if ch.title else f"Part {len(parts) + 1}")
                    parts.append(cur)
                    words = 0
                cur.paragraphs.append(p)
                words += p.text.count(" ") + 1
        out.extend(parts)
    return out
