"""Turn (X)HTML into paragraphs. Shared by EPUB and the MOBI fallback."""
from __future__ import annotations

import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from .textproc import clean_ws

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCKS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "dt", "dd",
    "td", "th", "figcaption", "caption", "address", "section", "article", "aside",
    "header", "footer", "center", "table", "tr", "ul", "ol", "dl", "nav", "body",
}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
DROP = ["script", "style", "rt", "rp", "head", "title", "svg", "math", "noscript"]


@dataclass
class Block:
    text: str
    kind: str          # "p" or "h"
    marker: str | None  # id of a TOC anchor that starts right before this block


def soup_of(data: bytes | str) -> BeautifulSoup:
    try:
        return BeautifulSoup(data, "lxml")
    except Exception:
        return BeautifulSoup(data, "html.parser")


def extract_blocks(data: bytes | str, anchors: set[str] | None = None) -> list[Block]:
    """Leaf block elements in document order. ``anchors``: element ids to report as markers."""
    soup = soup_of(data)
    for tag in soup(DROP):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with(" ")
    # footnote reference markers like <sup><a href="#fn1">1</a></sup>
    for sup in soup.find_all("sup"):
        txt = sup.get_text(strip=True)
        if len(txt) <= 3 and (sup.find("a") or txt.isdigit() or txt in {"*", "†", "‡"}):
            sup.decompose()
    for img in soup.find_all("img"):
        img.decompose()

    anchors = anchors or set()
    blocks: list[Block] = []
    pending: str | None = None
    consumed: set[str] = set()
    root = soup.body or soup

    def anchor_id(t) -> str | None:
        for key in ("id", "name"):
            v = t.get(key)
            if v and v in anchors and v not in consumed:
                return v
        return None

    for el in root.find_all(True):
        aid = anchor_id(el)
        if aid and pending is None:
            pending = aid
        if el.name not in BLOCKS:
            continue
        if el.find(lambda t: t.name in BLOCKS):
            continue  # not a leaf block; its children will be visited
        marker = pending
        if marker is None and anchors:
            inner = el.find(lambda t: anchor_id(t) is not None)
            if inner is not None:
                marker = anchor_id(inner)
        text = clean_ws(el.get_text(""))
        if not text:
            pending = marker      # empty anchor block: the chapter starts at the next one
            continue
        if marker:
            consumed.add(marker)
        kind = "h" if el.name in HEADINGS else "p"
        blocks.append(Block(text, kind, marker))
        pending = None
    if not blocks:
        text = clean_ws(root.get_text(" "))
        if text:
            blocks.append(Block(text, "p", pending))
    return blocks
