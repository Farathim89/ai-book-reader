"""TXT and RTF books: decoding, Project Gutenberg clean-up, paragraphs and chapter detection."""
from __future__ import annotations

import re
from pathlib import Path

from .model import Book, Chapter, Paragraph
from .textproc import clean_ws

CHAPTER_RE = re.compile(
    r"^\s*(chapter|prologue|epilogue|part|book|section|interlude|afterword|foreword|preface|introduction)"
    r"\b[\w\s.,:;'’\-–—!?()]{0,60}$",
    re.I,
)
ROMAN_RE = re.compile(r"^\s*[IVXLC]{1,7}\.?\s*$")
GUT_START = re.compile(r"^\s*\*{3}\s*START OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
GUT_END = re.compile(r"^\s*\*{3}\s*END OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            continue
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return raw.decode("cp1252", "replace")


def title_author_from_name(path: str) -> tuple[str, str]:
    stem = Path(path).name
    for ext in (".fb2.zip", ".txt", ".rtf", ".docx", ".pdf", ".epub", ".mobi", ".azw3", ".azw", ".cbz", ".fb2"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    stem = stem.replace("_", " ").strip()
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return t.strip(), a.strip()
    return stem, ""


def text_to_book(text: str, path: str, fmt: str) -> Book:
    title, author = title_author_from_name(path)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Project Gutenberg header / footer
    head = text[:5000]
    m = re.search(r"^Title:\s*(.+)$", head, re.M)
    if m:
        title = m.group(1).strip()
    m = re.search(r"^Author:\s*(.+)$", head, re.M)
    if m:
        author = m.group(1).strip()
    s = GUT_START.search(text)
    if s:
        text = text[s.end():]
    e = GUT_END.search(text)
    if e:
        text = text[:e.start()]

    lines = text.split("\n")
    blocks: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    if not blocks:
        return Book(path=path, fmt=fmt, title=title, author=author)

    avg_lines = sum(len(b) for b in blocks) / len(blocks)
    paras: list[str] = []
    if avg_lines >= 1.6:
        # hard-wrapped text: blank lines separate paragraphs
        for b in blocks:
            if len(b) > 1 and all(CHAPTER_RE.match(x) or len(x) < 40 for x in b[:1]) and CHAPTER_RE.match(b[0]):
                paras.append(b[0])
                b = b[1:]
            joined = " ".join(b)
            joined = re.sub(r"(\w)- (\w)", r"\1\2", joined)  # de-hyphenate wrapped words
            paras.append(joined)
    else:
        paras = [ln for b in blocks for ln in b]

    chapters: list[Chapter] = []
    current = Chapter(title or "Start")
    chapters.append(current)
    heads = 0
    for raw in paras:
        t = clean_ws(raw)
        if not t:
            continue
        is_head = len(t) <= 70 and (CHAPTER_RE.match(t) or ROMAN_RE.match(t))
        if is_head:
            heads += 1
            current = Chapter(t.rstrip(".").strip())
            chapters.append(current)
            current.paragraphs.append(Paragraph(text=t, kind="h"))
        else:
            current.paragraphs.append(Paragraph(text=t))
    chapters = [c for c in chapters if c.paragraphs]
    if heads < 2:
        chapters = _chunk_chapters([p for c in chapters for p in c.paragraphs])
    return Book(path=path, fmt=fmt, title=title, author=author, chapters=chapters)


def _chunk_chapters(paras: list[Paragraph], words_per_part: int = 5000) -> list[Chapter]:
    chapters: list[Chapter] = []
    cur: Chapter | None = None
    words = 0
    for p in paras:
        if cur is None or words >= words_per_part:
            cur = Chapter(f"Part {len(chapters) + 1}")
            chapters.append(cur)
            words = 0
        cur.paragraphs.append(p)
        words += p.text.count(" ") + 1
    if len(chapters) == 1:
        chapters[0].title = "Full text"
    return chapters


def load_txt(path: str) -> Book:
    return text_to_book(decode_bytes(Path(path).read_bytes()), path, "txt")


def load_rtf(path: str) -> Book:
    from striprtf.striprtf import rtf_to_text
    raw = decode_bytes(Path(path).read_bytes())
    text = rtf_to_text(raw, errors="ignore")
    # RTF paragraphs are single line breaks; make them blank-line separated
    text = re.sub(r"\n\s*", "\n\n", text)
    return text_to_book(text, path, "rtf")
