"""Format detection and loading."""
from __future__ import annotations

from pathlib import Path

from .model import Book, Chapter, Paragraph, Quote, Sentence  # noqa: F401

EXTS = {
    ".epub": "epub", ".pdf": "pdf", ".txt": "txt", ".docx": "docx", ".rtf": "rtf",
    ".fb2": "fb2", ".mobi": "mobi", ".azw3": "azw3", ".azw": "mobi", ".prc": "mobi", ".cbz": "cbz",
}
FILE_FILTER = "Books (*.epub *.pdf *.txt *.docx *.rtf *.fb2 *.fb2.zip *.mobi *.azw3 *.azw *.prc *.cbz)"


class BookError(Exception):
    """A book that cannot be opened (damaged, DRM-protected, unsupported...)."""


def detect_format(path: str | Path) -> str | None:
    name = str(path).lower()
    if name.endswith(".fb2.zip"):
        return "fb2"
    return EXTS.get(Path(name).suffix)


def parse_book(path: str, ocr: dict | None = None) -> Book:
    """Parse a file into a Book (without building the sentence index)."""
    fmt = detect_format(path)
    try:
        if fmt == "epub":
            from .epub import load_epub
            return load_epub(path)
        if fmt == "pdf":
            from .pdf import load_pdf
            return load_pdf(path, {int(k): v for k, v in (ocr or {}).items()})
        if fmt == "txt":
            from .plaintext import load_txt
            return load_txt(path)
        if fmt == "rtf":
            from .plaintext import load_rtf
            return load_rtf(path)
        if fmt == "docx":
            from .docx import load_docx
            return load_docx(path)
        if fmt == "fb2":
            from .fb2 import load_fb2
            return load_fb2(path)
        if fmt in ("mobi", "azw3"):
            from .kindle import load_kindle
            return load_kindle(path)
        if fmt == "cbz":
            from .cbz import load_cbz
            return load_cbz(path, {int(k): v for k, v in (ocr or {}).items()})
    except BookError:
        raise
    except Exception as e:  # turn parser crashes into a readable message
        raise BookError(str(e) or e.__class__.__name__) from e
    raise BookError(f"Unsupported file type: {Path(path).suffix}")


def load_book(path: str, ocr: dict | None = None) -> Book:
    book = parse_book(path, ocr)
    book.build_index()
    return book


def read_meta(path: str) -> tuple[str, str, bytes | None]:
    """Quick (title, author, cover bytes) for the library."""
    fmt = detect_format(path)
    from .plaintext import title_author_from_name
    try:
        if fmt == "epub":
            from .epub import epub_meta
            t, a, c = epub_meta(path)
        elif fmt == "pdf":
            from .pdf import pdf_meta
            t, a, c = pdf_meta(path)
        elif fmt == "fb2":
            from .fb2 import fb2_meta
            t, a, c = fb2_meta(path)
        elif fmt == "docx":
            from .docx import docx_meta
            t, a, c = docx_meta(path)
        elif fmt in ("mobi", "azw3"):
            from .kindle import kindle_meta
            t, a, c = kindle_meta(path)
        elif fmt == "cbz":
            from .cbz import cbz_meta
            t, a, c = cbz_meta(path)
        else:
            t, a = title_author_from_name(path)
            c = None
    except Exception:
        t, a = title_author_from_name(path)
        c = None
    ft, fa = title_author_from_name(path)
    cal = calibre_meta(path)
    if cal:   # a Calibre library folder: its metadata is what the user curated
        t, a, c = cal[0] or t, cal[1] or a, c or cal[2]
    return t or ft, a or fa, c


def calibre_meta(path: str) -> tuple[str, str, bytes | None] | None:
    """Title, authors and cover from a Calibre library book folder (metadata.opf + cover.jpg)."""
    folder = Path(path).parent
    opf = folder / "metadata.opf"
    if not opf.is_file():
        return None
    try:
        from lxml import etree
        root = etree.parse(str(opf), etree.XMLParser(recover=True, resolve_entities=False, no_network=True)).getroot()
        dc = "{http://purl.org/dc/elements/1.1/}"
        title = (root.findtext(f".//{dc}title") or "").strip()
        authors = [e.text.strip() for e in root.iter(f"{dc}creator") if e.text and e.text.strip()]
    except Exception:
        return None
    cover = None
    for name in ("cover.jpg", "cover.jpeg", "cover.png"):
        if (folder / name).is_file():
            cover = (folder / name).read_bytes()
            break
    return title, ", ".join(dict.fromkeys(authors)), cover
