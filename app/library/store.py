"""Per-book data on disk: parse cache, OCR results, character analysis.

data/books/<id>/book.json        parsed structure (fast re-open, stable sentence numbers)
data/books/<id>/ocr.json         {page: [{"text", "box"}...]} or {page: ["paragraph", ...]} for PDFs
data/books/<id>/characters.json  {"characters": {...}, "speakers": {qid: name}, "done_chapters": [...]}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..formats import Book, load_book, parse_book
from ..paths import sub

PARSER_VERSION = 3


def book_dir(book_id: int) -> Path:
    return sub("books", str(book_id))


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, data) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


# ---- OCR ---------------------------------------------------------------------
def load_ocr(book_id: int) -> dict[int, list]:
    return {int(k): v for k, v in _read_json(book_dir(book_id) / "ocr.json", {}).items()}


def save_ocr(book_id: int, ocr: dict[int, list]) -> None:
    _write_json(book_dir(book_id) / "ocr.json", {str(k): v for k, v in ocr.items()})


# ---- characters ----------------------------------------------------------------
def load_characters(book_id: int) -> dict:
    d = _read_json(book_dir(book_id) / "characters.json", {})
    d.setdefault("characters", {})
    d.setdefault("speakers", {})
    d.setdefault("done_chapters", [])
    return d


def save_characters(book_id: int, data: dict) -> None:
    _write_json(book_dir(book_id) / "characters.json", data)


def apply_speakers(book: Book, data: dict) -> None:
    speakers = {int(k): v for k, v in data.get("speakers", {}).items()}
    for ch in book.chapters:
        for p in ch.paragraphs:
            for q in p.quotes:
                q.speaker = speakers.get(q.qid)


# ---- parse cache -------------------------------------------------------------
def open_book(book_id: int, path: str) -> Book:
    """Load a book, using the parse cache when the file has not changed."""
    st = os.stat(path)
    ocr = load_ocr(book_id)
    key = {"v": PARSER_VERSION, "size": st.st_size, "mtime": int(st.st_mtime), "ocr": len(ocr)}
    cache = book_dir(book_id) / "book.json"
    cached = _read_json(cache, None)
    if cached and cached.get("key") == key:
        book = Book.from_dict(cached["book"])
        book.build_index()
    else:
        book = parse_book(path, ocr)
        book.build_index()
        _write_json(cache, {"key": key, "book": book.to_dict()})
    apply_speakers(book, load_characters(book_id))
    return book


def invalidate(book_id: int) -> None:
    (book_dir(book_id) / "book.json").unlink(missing_ok=True)


__all__ = ["open_book", "load_book", "load_ocr", "save_ocr", "load_characters", "save_characters",
           "apply_speakers", "book_dir", "invalidate"]
