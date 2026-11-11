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
        d["progress"] = (d["position"] / d["total"]) if d.get("total") else 0.0
        return d

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


def scan_folder(folder: str):
    """Yield supported book files under ``folder``."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if detect_format(f):
                yield os.path.join(root, f)
