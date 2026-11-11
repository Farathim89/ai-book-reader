"""CBZ comics. Pages are images; the text comes from OCR (see app/ai/ocr.py) and is passed in
as {page_index: [{"text": ..., "box": [x0, y0, x1, y1]}, ...]}."""
from __future__ import annotations

import re
import zipfile

from lxml import etree

from .model import Book, Chapter, Paragraph
from .plaintext import title_author_from_name
from .textproc import clean_ws

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def list_pages(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith(IMG_EXT) and not n.startswith("__MACOSX") and not n.endswith("/")]
    return sorted(names, key=_natural)


def read_page(path: str, name: str) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read(name)


def _comicinfo(path: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith("comicinfo.xml")), None)
            if not name:
                return "", ""
            root = etree.fromstring(z.read(name), etree.XMLParser(recover=True, resolve_entities=False))
    except Exception:
        return "", ""
    get = lambda tag: clean_ws(root.findtext(tag) or "")  # noqa: E731
    series, number, title = get("Series"), get("Number"), get("Title")
    full = series
    if number:
        full = f"{full} #{number}" if full else f"#{number}"
    if title:
        full = f"{full} – {title}" if full else title
    return full, get("Writer")


def load_cbz(path: str, ocr: dict[int, list[dict]] | None = None) -> Book:
    ocr = ocr or {}
    pages = list_pages(path)
    ft, fa = title_author_from_name(path)
    ct, ca = _comicinfo(path)
    folders = sorted({p.rsplit("/", 1)[0] if "/" in p else "" for p in pages}, key=_natural)
    use_folders = len(folders) > 1
    chapters: dict[str, Chapter] = {}
    for i, name in enumerate(pages):
        folder = (name.rsplit("/", 1)[0] if "/" in name else "") if use_folders else ""
        ch = chapters.get(folder)
        if ch is None:
            title = folder.rsplit("/", 1)[-1] if folder else (ct or ft)
            ch = chapters[folder] = Chapter(title or "Pages")
        for b in ocr.get(i, []):
            t = clean_ws(b.get("text", ""))
            if t:
                ch.paragraphs.append(Paragraph(text=t, page=i, box=b.get("box") or None))
    book = Book(path=path, fmt="cbz", title=ct or ft, author=ca or fa,
                chapters=list(chapters.values()), kind="comic", pages=pages)
    book.ocr_pages_needed = [i for i in range(len(pages)) if i not in ocr]  # type: ignore[attr-defined]
    if pages:
        try:
            book.cover = read_page(path, pages[0])
        except Exception:
            book.cover = None
    return book


def cbz_meta(path: str) -> tuple[str, str, bytes | None]:
    ft, fa = title_author_from_name(path)
    ct, ca = _comicinfo(path)
    pages = list_pages(path)
    cover = read_page(path, pages[0]) if pages else None
    return ct or ft, ca or fa, cover
