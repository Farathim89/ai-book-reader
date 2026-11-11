"""What the library pages show: filtering, sorting, series / author grouping, Home shelves.
Pure functions over the book rows from Library.books() (plus a "library" key)."""
from __future__ import annotations

import datetime
import random
import re

from .libraries import ALL

FILTERS = ["All", "In progress", "Not started", "Finished", "Has character voices",
           "EPUB", "PDF", "Kindle (MOBI/AZW3)", "Comics (CBZ)", "Other formats"]
SORTS = ["Title", "Author", "Series", "Recently added", "Recently read", "Progress", "Length"]


def fmt_index(idx) -> str:
    if idx is None:
        return ""
    return f"{idx:g}"


IGNORE_ARTICLES = False       # library-catalogue style "The Hobbit" under H (setting "sort_ignore_articles")


def set_ignore_articles(on: bool) -> None:
    global IGNORE_ARTICLES
    IGNORE_ARTICLES = bool(on)


def sort_title(t: str) -> str:
    """Sort key: as written ("The Beginning..." under T), or without a leading The / A / An when
    IGNORE_ARTICLES is on; numbers sort naturally."""
    t = (t or "").lower().strip()
    if IGNORE_ARTICLES:
        t = re.sub(r"^(the|a|an)\s+", "", t)
    # natural numbers: "vol. 2" before "vol. 10"
    return re.sub(r"\d+(\.\d+)?", lambda m: f"{float(m.group()):012.3f}", t)


def authors_of(r: dict) -> list[str]:
    raw = r.get("author") or ""
    parts = [a.strip() for a in re.split(r",|&| and |;", raw) if a.strip()]
    return parts or ["Unknown"]


def in_library(rows: list[dict], library: str) -> list[dict]:
    return rows if library in (None, "", ALL) else [r for r in rows if r.get("library") == library]


def search(rows: list[dict], text: str) -> list[dict]:
    t = (text or "").lower().strip()
    if not t:
        return rows
    words = t.split()
    out = []
    for r in rows:
        hay = " ".join(str(r.get(k) or "") for k in ("title", "author", "series", "publisher")).lower()
        if all(w in hay for w in words):
            out.append(r)
    return out


def apply_filter(rows: list[dict], flt: str) -> list[dict]:
    if flt == "In progress":
        return [r for r in rows if r.get("started") and not r.get("finished")]
    if flt == "Not started":
        return [r for r in rows if not r.get("started") and not r.get("finished")]
    if flt == "Finished":
        return [r for r in rows if r.get("finished")]
    if flt == "Has character voices":
        return [r for r in rows if (r.get("settings") or {}).get("use_characters")]
    if flt == "EPUB":
        return [r for r in rows if r.get("fmt") == "epub"]
    if flt == "PDF":
        return [r for r in rows if r.get("fmt") == "pdf"]
    if flt == "Kindle (MOBI/AZW3)":
        return [r for r in rows if r.get("fmt") in ("mobi", "azw3")]
    if flt == "Comics (CBZ)":
        return [r for r in rows if r.get("fmt") == "cbz"]
    if flt == "Other formats":
        return [r for r in rows if r.get("fmt") not in ("epub", "pdf", "mobi", "azw3", "cbz")]
    return rows


def apply_sort(rows: list[dict], key: str, desc: bool = False) -> list[dict]:
    if key == "Author":
        k = lambda r: (authors_of(r)[0].lower(), (r.get("series") or "").lower(), r.get("series_index") or 0,  # noqa: E731
                       sort_title(r.get("title")))
    elif key == "Series":
        k = lambda r: ((r.get("series") or "~").lower(), r.get("series_index") or 0, sort_title(r.get("title")))  # noqa: E731
    elif key == "Recently added":
        k = lambda r: -(r.get("added") or 0)  # noqa: E731
    elif key == "Recently read":
        k = lambda r: -(r.get("opened") or 0)  # noqa: E731
    elif key == "Progress":
        k = lambda r: -(1.0 if r.get("finished") else r.get("progress") or 0)  # noqa: E731
    elif key == "Length":
        k = lambda r: r.get("chars") or 0  # noqa: E731
    else:
        k = lambda r: sort_title(r.get("title"))  # noqa: E731
    out = sorted(rows, key=k)
    return list(reversed(out)) if desc else out


def series_groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("series"):
            groups.setdefault(r["series"], []).append(r)
    out = []
    for name, books in groups.items():
        books.sort(key=lambda r: (r.get("series_index") is None, r.get("series_index") or 0, sort_title(r["title"])))
        out.append((name, books))
    out.sort(key=lambda g: sort_title(g[0]))
    return out


def author_groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        for a in authors_of(r):
            groups.setdefault(a, []).append(r)
    out = [(a, apply_sort(books, "Series")) for a, books in groups.items()]
    out.sort(key=lambda g: sort_title(g[0]))
    return out


def home_shelves(rows: list[dict], limit: int = 30) -> list[tuple[str, list[dict]]]:
    shelves: list[tuple[str, list[dict]]] = []
    reading = sorted([r for r in rows if r.get("started") and not r.get("finished")],
                     key=lambda r: -(r.get("opened") or 0))
    shelves.append(("Continue Reading", reading[:limit]))

    nxt = []
    for name, books in series_groups(rows):
        if not any(b.get("finished") for b in books):
            continue
        if any(b.get("started") and not b.get("finished") for b in books):
            continue     # already in Continue Reading
        last_done = max(i for i, b in enumerate(books) if b.get("finished"))
        following = [b for b in books[last_done + 1:] if not b.get("finished") and not b.get("started")]
        if following:
            nxt.append((max(b.get("opened") or 0 for b in books), following[0]))
    nxt.sort(key=lambda x: -x[0])
    shelves.append(("Continue Series", [b for _, b in nxt][:limit]))

    added = sorted(rows, key=lambda r: -(r.get("added") or 0))
    shelves.append(("Recently Added", added[:limit]))

    fresh = [r for r in rows if not r.get("started") and not r.get("finished")]
    rnd = random.Random(datetime.date.today().toordinal())   # same picks all day
    shelves.append(("Discover", rnd.sample(fresh, min(limit, len(fresh)))))

    again = sorted([r for r in rows if r.get("finished")], key=lambda r: -(r.get("opened") or 0))
    shelves.append(("Read Again", again[:limit]))
    return [(t, b) for t, b in shelves if b]
