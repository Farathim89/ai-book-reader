"""MOBI / AZW3 / AZW / PRC. Converted to EPUB with Calibre's ebook-convert (best result)
or unpacked with the ``mobi`` package when Calibre is missing. DRM-protected files fail."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from ..paths import sub
from .model import Book

CALIBRE_DIRS = [r"C:\Program Files\Calibre2", r"C:\Program Files (x86)\Calibre2",
                os.path.expandvars(r"%LOCALAPPDATA%\Calibre2")]


class KindleError(Exception):
    pass


def calibre_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in CALIBRE_DIRS:
        p = Path(d) / f"{name}.exe"
        if p.exists():
            return str(p)
    return None


def _key(path: str) -> str:
    st = os.stat(path)
    return hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def converted_epub(path: str) -> str:
    """Path to an EPUB version of the Kindle book (cached)."""
    out = sub("cache", "convert") / f"{_key(path)}.epub"
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    tool = calibre_tool("ebook-convert")
    if tool:
        r = subprocess.run([tool, path, str(out)], capture_output=True, text=True, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=600)
        if r.returncode == 0 and out.exists():
            return str(out)
        msg = (r.stdout + r.stderr)
        if "DRM" in msg:
            raise KindleError("This Kindle book is DRM-protected. Only DRM-free books can be opened.")
        raise KindleError("Calibre could not convert this book:\n" + msg.strip()[-600:])
    try:
        import mobi
    except ImportError as e:
        raise KindleError("Install Calibre (or the 'mobi' Python package) to open Kindle books.") from e
    try:
        tempdir, extracted = mobi.extract(path)
    except Exception as e:
        raise KindleError(f"Could not unpack this Kindle book (it may be DRM-protected): {e}") from e
    ext = Path(extracted).suffix.lower()
    if ext == ".epub":
        shutil.copyfile(extracted, out)
        shutil.rmtree(tempdir, ignore_errors=True)
        return str(out)
    # old MOBI: a single HTML file -> wrap it as a minimal book via the HTML path
    html_out = out.with_suffix(".html")
    shutil.copyfile(extracted, html_out)
    shutil.rmtree(tempdir, ignore_errors=True)
    return str(html_out)


def load_kindle(path: str) -> Book:
    from .epub import load_epub
    fmt = Path(path).suffix.lower().lstrip(".")
    conv = converted_epub(path)
    if conv.endswith(".epub"):
        book = load_epub(conv, fmt=fmt)
    else:
        book = _load_html(conv, fmt)
    book.path = path
    if not book.title:
        from .plaintext import title_author_from_name
        book.title, a = title_author_from_name(path)
        book.author = book.author or a
    return book


def _load_html(html_path: str, fmt: str) -> Book:
    from .htmltext import extract_blocks
    from .model import Chapter, Paragraph
    data = Path(html_path).read_bytes().replace(b"<mbp:pagebreak", b"<hr class=\"pb\"")
    blocks = extract_blocks(data)
    chapters: list[Chapter] = []
    cur = None
    for b in blocks:
        if b.kind == "h" or cur is None:
            cur = Chapter(b.text[:70] if b.kind == "h" else "Start")
            chapters.append(cur)
        cur.paragraphs.append(Paragraph(text=b.text, kind=b.kind))
    return Book(path=html_path, fmt=fmt, title="", chapters=chapters)


def kindle_meta(path: str) -> tuple[str, str, bytes | None]:
    from .plaintext import title_author_from_name
    title, author = title_author_from_name(path)
    cover = None
    tool = calibre_tool("ebook-meta")
    if tool:
        tmp = sub("cache", "tmp") / f"{_key(path)}.jpg"
        try:
            r = subprocess.run([tool, path, f"--get-cover={tmp}"], capture_output=True, text=True,
                               errors="replace", timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in r.stdout.splitlines():
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip()
                if k == "title" and v:
                    title = v
                elif k.startswith("author") and v:
                    author = v.split("[")[0].strip().replace(" & ", ", ")
            if tmp.exists():
                cover = tmp.read_bytes()
                tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return title, author, cover
