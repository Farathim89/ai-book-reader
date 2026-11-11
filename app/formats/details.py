"""Extra book details for the library pages: series + volume number, description, publisher, year.

Sources, best first: Calibre's metadata.opf next to the file, the book's own metadata (EPUB OPF,
FB2 title-info, CBZ ComicInfo.xml), and finally the title itself ("Name, Vol. 3", "Name v03")."""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

from lxml import etree

from . import detect_format

_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=True)
DC = "{http://purl.org/dc/elements/1.1/}"
VOL_RE = re.compile(r"^(?P<series>.+?)(?:[\s,:\-–]+)(?:vol(?:ume)?\.?|v|book|#|part)\s*(?P<num>\d{1,4}(?:\.\d+)?)\b",
                    re.I)


def strip_html(text: str) -> str:
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _num(v) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _from_opf_root(root) -> dict:
    d: dict = {}
    if root is None:
        return d
    for m in root.iter("{*}meta"):
        name, content = m.get("name"), m.get("content")
        if name == "calibre:series" and content:
            d["series"] = content.strip()
        elif name == "calibre:series_index" and content:
            d["series_index"] = _num(content)
    if "series" not in d:   # EPUB3 belongs-to-collection
        for m in root.iter("{*}meta"):
            if m.get("property") == "belongs-to-collection" and (m.text or "").strip():
                d["series"] = m.text.strip()
                mid = m.get("id")
                for r in root.iter("{*}meta"):
                    if r.get("refines") == f"#{mid}" and r.get("property") == "group-position":
                        d["series_index"] = _num(r.text)
                break
    desc = root.findtext(f".//{DC}description")
    if desc:
        d["description"] = strip_html(desc)
    pub = root.findtext(f".//{DC}publisher")
    if pub:
        d["publisher"] = pub.strip()
    date = root.findtext(f".//{DC}date") or ""
    m = re.match(r"\s*(\d{4})", date)
    if m and m.group(1) not in ("0101", "0100"):
        d["year"] = int(m.group(1))
    return d


def _calibre(path: str) -> dict:
    opf = Path(path).parent / "metadata.opf"
    if not opf.is_file():
        return {}
    try:
        return _from_opf_root(etree.parse(str(opf), _PARSER).getroot())
    except Exception:
        return {}


def _embedded(path: str, fmt: str) -> dict:
    try:
        if fmt == "epub":
            from .epub import Epub
            return _from_opf_root(Epub(path).opf)
        if fmt == "fb2":
            from .fb2 import _read
            root = etree.fromstring(_read(path), _PARSER)
            d: dict = {}
            for e in root.iter("{*}sequence"):
                if e.get("name"):
                    d["series"], d["series_index"] = e.get("name").strip(), _num(e.get("number"))
                    break
            ann = next(root.iter("{*}annotation"), None)
            if ann is not None:
                d["description"] = strip_html(etree.tostring(ann, encoding="unicode"))
            return d
        if fmt == "cbz":
            with zipfile.ZipFile(path) as z:
                name = next((n for n in z.namelist() if n.lower().endswith("comicinfo.xml")), None)
                if not name:
                    return {}
                root = etree.fromstring(z.read(name), _PARSER)
            d = {}
            if (root.findtext("Series") or "").strip():
                d["series"] = root.findtext("Series").strip()
                d["series_index"] = _num(root.findtext("Number") or root.findtext("Volume"))
            if root.findtext("Summary"):
                d["description"] = strip_html(root.findtext("Summary"))
            if root.findtext("Publisher"):
                d["publisher"] = root.findtext("Publisher").strip()
            if (root.findtext("Year") or "").strip().isdigit():
                d["year"] = int(root.findtext("Year"))
            return d
    except Exception:
        return {}
    return {}


def series_from_title(title: str) -> tuple[str, float] | None:
    m = VOL_RE.match(title or "")
    if not m:
        return None
    series = m.group("series").strip(" ,:-–")
    return (series, float(m.group("num"))) if len(series) >= 2 else None


def read_details(path: str, title: str = "") -> dict:
    fmt = detect_format(path) or ""
    d = _embedded(path, fmt)
    for k, v in _calibre(path).items():   # Calibre (curated by the user) wins
        if v not in (None, ""):
            d[k] = v
    if not d.get("series"):
        guess = series_from_title(title or Path(path).stem)
        if guess:
            d["series"], d["series_index"] = guess
    return d
