"""Word .docx books: paragraphs, heading-based chapters, core properties."""
from __future__ import annotations

import re

from .model import Book, Chapter, Paragraph
from .plaintext import CHAPTER_RE, _chunk_chapters, title_author_from_name
from .textproc import clean_ws

_LEVEL = re.compile(r"heading\s*(\d)", re.I)


def _level(style_name: str) -> int | None:
    if not style_name:
        return None
    if style_name.lower() == "title":
        return 0
    m = _LEVEL.match(style_name)
    return int(m.group(1)) if m else None


def load_docx(path: str) -> Book:
    import docx
    d = docx.Document(path)
    props = d.core_properties
    ft, fa = title_author_from_name(path)
    title = clean_ws(props.title or "") or ft
    author = clean_ws(props.author or "") or fa

    items: list[tuple[str, int | None]] = []
    for p in d.paragraphs:
        t = clean_ws(p.text)
        if t:
            items.append((t, _level(p.style.name if p.style is not None else "")))
    levels = [lv for _, lv in items if lv and lv > 0]
    chap_level = min(levels) if levels else None

    chapters: list[Chapter] = []
    cur = Chapter(title or "Start")
    chapters.append(cur)
    for t, lv in items:
        is_chapter = (chap_level is not None and lv == chap_level) or (
            chap_level is None and len(t) <= 70 and CHAPTER_RE.match(t))
        if lv == 0 and not title:
            title = t
        if is_chapter:
            cur = Chapter(t)
            chapters.append(cur)
        cur.paragraphs.append(Paragraph(text=t, kind="h" if lv is not None else "p"))
    chapters = [c for c in chapters if c.paragraphs]
    if len(chapters) <= 1:
        chapters = _chunk_chapters([p for c in chapters for p in c.paragraphs])
    return Book(path=path, fmt="docx", title=title, author=author, chapters=chapters)


def docx_meta(path: str) -> tuple[str, str, bytes | None]:
    import docx
    d = docx.Document(path)
    ft, fa = title_author_from_name(path)
    props = d.core_properties
    cover = None
    try:  # first embedded picture as cover
        for rel in d.part.rels.values():
            if "image" in rel.reltype:
                cover = rel.target_part.blob
                break
    except Exception:
        pass
    return clean_ws(props.title or "") or ft, clean_ws(props.author or "") or fa, cover
