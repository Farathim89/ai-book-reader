"""FictionBook 2 (.fb2 and .fb2.zip)."""
from __future__ import annotations

import base64
import zipfile

from lxml import etree

from .model import Book, Chapter, Paragraph
from .plaintext import title_author_from_name
from .textproc import clean_ws

_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)
XLINK = "{http://www.w3.org/1999/xlink}href"


def _read(path: str) -> bytes:
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".fb2")), None)
            if not name:
                raise ValueError("No .fb2 file inside the zip")
            return z.read(name)
    with open(path, "rb") as f:
        return f.read()


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _text(el) -> str:
    return clean_ws("".join(el.itertext()))


def _meta(root) -> tuple[str, str, bytes | None]:
    title, author, cover = "", "", None
    ti = next((e for e in root.iter() if _local(e) == "title-info"), None)
    if ti is not None:
        for e in ti:
            ln = _local(e)
            if ln == "book-title" and not title:
                title = _text(e)
            elif ln == "author" and not author:
                parts = [_text(x) for x in e if _local(x) in ("first-name", "middle-name", "last-name")]
                author = " ".join(p for p in parts if p) or _text(e)
            elif ln == "coverpage":
                img = next((x for x in e.iter() if _local(x) == "image"), None)
                if img is not None:
                    ref = (img.get(XLINK) or img.get("href") or "").lstrip("#")
                    for b in root.iter():
                        if _local(b) == "binary" and b.get("id") == ref and b.text:
                            try:
                                cover = base64.b64decode(b.text)
                            except Exception:
                                cover = None
                            break
    return title, author, cover


def _paragraphs(section) -> list[Paragraph]:
    out: list[Paragraph] = []
    for el in section.iter():
        ln = _local(el)
        if ln == "title":
            t = _text(el)
            if t:
                out.append(Paragraph(text=t, kind="h"))
        elif ln in ("p", "v", "subtitle", "text-author"):
            # skip paragraphs that are inside a title (already taken as a whole)
            if any(_local(a) == "title" for a in el.iterancestors()):
                continue
            t = _text(el)
            if t:
                out.append(Paragraph(text=t, kind="h" if ln == "subtitle" else "p"))
    return out


def _sections(parent) -> list:
    return [c for c in parent if _local(c) == "section"]


def _collect(section, depth: int, chapters: list[Chapter]) -> None:
    subs = _sections(section)
    title_el = next((c for c in section if _local(c) == "title"), None)
    title = _text(title_el) if title_el is not None else ""
    own = [c for c in section if _local(c) not in ("section", "title")]
    if subs and depth < 2 and not any(_local(c) == "p" for c in own):
        # container section (e.g. "Part One") - its title becomes a heading of the first child
        first_index = len(chapters)
        for s in subs:
            _collect(s, depth + 1, chapters)
        if title and len(chapters) > first_index:
            chapters[first_index].paragraphs.insert(0, Paragraph(text=title, kind="h"))
            chapters[first_index].title = f"{title} – {chapters[first_index].title}".strip(" –")
        return
    ch = Chapter(title or f"Section {len(chapters) + 1}", _paragraphs(section))
    chapters.append(ch)


def load_fb2(path: str) -> Book:
    root = etree.fromstring(_read(path), _PARSER)
    title, author, cover = _meta(root)
    ft, fa = title_author_from_name(path)
    bodies = [b for b in root if _local(b) == "body" and b.get("name") not in ("notes", "comments")]
    chapters: list[Chapter] = []
    for body in bodies:
        secs = _sections(body)
        if not secs:
            chapters.append(Chapter(title or ft, _paragraphs(body)))
        for s in secs:
            _collect(s, 0, chapters)
    book = Book(path=path, fmt="fb2", title=title or ft, author=author or fa,
                chapters=[c for c in chapters if c.paragraphs])
    book.cover = cover
    return book


def fb2_meta(path: str) -> tuple[str, str, bytes | None]:
    root = etree.fromstring(_read(path), _PARSER)
    title, author, cover = _meta(root)
    ft, fa = title_author_from_name(path)
    return title or ft, author or fa, cover
