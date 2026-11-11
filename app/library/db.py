"""SQLite library: books, reading positions and per-book settings."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..formats import detect_format, read_meta
from ..paths import data_dir, sub

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id       INTEGER PRIMARY KEY,
    path     TEXT UNIQUE NOT NULL,
    fmt      TEXT,
    title    TEXT,
    author   TEXT,
    cover    TEXT,
    added    REAL,
    opened   REAL DEFAULT 0,
    position INTEGER DEFAULT 0,
    total    INTEGER DEFAULT 0,
    settings TEXT DEFAULT '{}'
);
"""
# columns added after 1.0 (created on old databases by _migrate)
EXTRA_COLUMNS = {
    "series": "TEXT", "series_index": "REAL", "description": "TEXT", "publisher": "TEXT",
    "year": "INTEGER", "chars": "INTEGER DEFAULT 0", "details": "INTEGER DEFAULT 0",
}
DETAILS_VERSION = 1


def cached_chars(book_id: int) -> int:
    """Characters of text (for length estimates) from the parse cache, 0 when not parsed yet."""
    import json
    p = data_dir() / "books" / str(book_id) / "book.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))["book"]
    except (OSError, ValueError, KeyError):
        return 0
    return sum(len(par[0]) for ch in d["chapters"] for par in ch["paragraphs"])


def save_cover(book_id: int, data: bytes | None) -> str | None:
    if not data:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((360, 540))
        out = sub("covers") / f"{book_id}.jpg"
        img.save(out, "JPEG", quality=88)
        return str(out)
    except Exception:
        return None


class Library:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "library.db")
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            have = {r[1] for r in self.conn.execute("PRAGMA table_info(books)")}
            for col, decl in EXTRA_COLUMNS.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE books ADD COLUMN {col} {decl}")
            self.conn.commit()

    # ---- queries ------------------------------------------------------------
    def books(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM books").fetchall()
        return [self._row(r) for r in rows]

    def get(self, book_id: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return self._row(r) if r else None

    def by_path(self, path: str) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM books WHERE path=?", (os.path.abspath(path),)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["settings"] = json.loads(d.get("settings") or "{}")
        except ValueError:
            d["settings"] = {}
        total = d.get("total") or 0
        d["progress"] = (d["position"] / max(1, total - 1)) if total > 1 else 0.0
        d["finished"] = total > 1 and d["position"] >= total - 1
        d["started"] = bool(d.get("opened")) and (d["position"] or 0) > 0
        return d

    def fill_details(self, book_id: int, with_length: bool = False) -> None:
        """Series, description, publisher, year (and, if asked, length from the parse cache - slow)."""
        from ..formats.details import read_details
        r = self.get(book_id)
        if not r:
            return
        d = read_details(r["path"], r["title"])
        fields = {k: d.get(k) for k in ("series", "series_index", "description", "publisher", "year")}
        fields["details"] = DETAILS_VERSION
        if with_length and not r.get("chars"):
            fields["chars"] = cached_chars(book_id)
        self.update(book_id, **fields)

    def needs_details(self) -> list[int]:
        with self.lock:
            return [row[0] for row in self.conn.execute(
                "SELECT id FROM books WHERE IFNULL(details, 0) < ?", (DETAILS_VERSION,))]

    # ---- changes ------------------------------------------------------------
    def add(self, path: str) -> int | None:
        """Add a book file. Returns the new id, or None when it is already in the library."""
        path = os.path.abspath(path)
        fmt = detect_format(path)
        if not fmt or self.by_path(path):
            return None
        title, author, cover = read_meta(path)
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO books (path, fmt, title, author, added) VALUES (?,?,?,?,?)",
                (path, fmt, title, author, time.time()))
            self.conn.commit()
            book_id = cur.lastrowid if cur.rowcount else None
        if book_id:
            cpath = save_cover(book_id, cover)
            if cpath:
                self.update(book_id, cover=cpath)
            try:
                self.fill_details(book_id)
            except Exception:
                pass
        return book_id

    def update(self, book_id: int, **fields) -> None:
        if not fields:
            return
        if "settings" in fields and not isinstance(fields["settings"], str):
            fields["settings"] = json.dumps(fields["settings"])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE books SET {cols} WHERE id=?", (*fields.values(), book_id))
            self.conn.commit()

    def update_settings(self, book_id: int, **values) -> dict:
        b = self.get(book_id)
        s = dict(b["settings"]) if b else {}
        s.update(values)
        self.update(book_id, settings=s)
        return s

    def remove(self, book_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM books WHERE id=?", (book_id,))
            self.conn.commit()
        cover = sub("covers") / f"{book_id}.jpg"
        cover.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(data_dir() / "books" / str(book_id), ignore_errors=True)

    def close(self) -> None:
        with self.lock:
            self.conn.close()


FORMAT_PREFERENCE = ["epub", "azw3", "mobi", "fb2", "docx", "pdf", "rtf", "txt", "cbz"]


def _stem(name: str) -> str:
    low = name.lower()
    for ext in (".fb2.zip",):
        if low.endswith(ext):
            return low[: -len(ext)]
    return os.path.splitext(low)[0]


def scan_folder(folder: str):
    """Yield supported book files under ``folder``. When a folder holds the same book in several
    formats (e.g. a Calibre library: book.epub + book.mobi + book.pdf) only the best one is used."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        best: dict[str, tuple[int, str]] = {}
        for f in files:
            fmt = detect_format(f)
            if not fmt:
                continue
            rank = FORMAT_PREFERENCE.index(fmt) if fmt in FORMAT_PREFERENCE else 99
            key = _stem(f)
            if key not in best or rank < best[key][0]:
                best[key] = (rank, f)
        for _, f in sorted(best.values(), key=lambda x: x[1].lower()):
            yield os.path.join(root, f)
