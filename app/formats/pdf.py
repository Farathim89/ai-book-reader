"""PDF books (PyMuPDF): text blocks -> paragraphs, header/footer removal, outline chapters.
Pages without a text layer (scans) are reported in ``book.ocr_pages_needed`` and filled
from OCR results when those are passed in."""
from __future__ import annotations

import re
from collections import Counter

from .model import Book, Chapter, Paragraph
from .plaintext import CHAPTER_RE, title_author_from_name
from .textproc import clean_ws

_END = re.compile("[.!?:\u201d\"\u2019)\\]]\\s*$")
_DIGITS = re.compile(r"\d+")


def _open(path: str):
    import pymupdf as fitz
    doc = fitz.open(path)
    if doc.needs_pass:
        raise ValueError("This PDF is password-protected.")
    return doc


def _block_text(raw: str) -> str:
    raw = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)       # hyphenated line breaks
    return clean_ws(raw.replace("\n", " "))


def pdf_page_count(path: str) -> int:
    with _open(path) as doc:
        return doc.page_count


def scanned_pages(path: str) -> list[int]:
    out = []
    with _open(path) as doc:
        for i, page in enumerate(doc):
            if len(page.get_text("text").strip()) < 20 and page.get_images():
                out.append(i)
    return out


def render_page_png(path: str, index: int, zoom: float = 2.0) -> bytes:
    import pymupdf as fitz
    with _open(path) as doc:
        page = doc[index]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


def load_pdf(path: str, ocr: dict[int, list[str]] | None = None) -> Book:
    ocr = ocr or {}
    doc = _open(path)
    try:
        meta = doc.metadata or {}
        ft, fa = title_author_from_name(path)
        title = clean_ws(meta.get("title") or "") or ft
        author = clean_ws(meta.get("author") or "") or fa

        pages: list[list[str]] = []
        needed: list[int] = []
        for i, page in enumerate(doc):
            blocks = [b for b in page.get_text("blocks", sort=True) if b[6] == 0]
            texts = [_block_text(b[4]) for b in blocks]
            texts = [t for t in texts if t]
            if sum(len(t) for t in texts) < 20 and page.get_images():
                if i in ocr:
                    texts = [clean_ws(t) for t in ocr[i] if clean_ws(t)]
                else:
                    needed.append(i)
                    texts = []
            pages.append(texts)

        # repeated headers / footers (first and last block of a page)
        edge = Counter()
        for texts in pages:
            for t in {*texts[:1], *texts[-1:]}:
                edge[_DIGITS.sub("#", t.lower())] += 1
        limit = max(3, int(len(pages) * 0.3))
        repeated = {k for k, v in edge.items() if v >= limit and len(k) < 120}

        def keep(t: str, pos: int, n: int) -> bool:
            if pos in (0, n - 1):
                if _DIGITS.sub("#", t.lower()) in repeated:
                    return False
                if re.fullmatch("(page\\s*)?[#\\divxlc\\s\\-–/of]{1,15}", t.lower()):
                    return False
            return True

        page_paras: list[list[str]] = []
        for texts in pages:
            n = len(texts)
            page_paras.append([t for k, t in enumerate(texts) if keep(t, k, n)])

        # chapters from the outline
        toc = [(lvl, clean_ws(t), max(0, p - 1)) for lvl, t, p in doc.get_toc(simple=True) if p > 0]
        starts: list[tuple[int, str]] = []
        if toc:
            levels = Counter(l for l, _, _ in toc)
            lvl = 1 if levels.get(1, 0) >= 2 or 2 not in levels else 2
            seen = set()
            for l, t, p in toc:
                if l == lvl and p not in seen:
                    starts.append((p, t or f"Page {p + 1}"))
                    seen.add(p)
            starts.sort()
    finally:
        doc.close()

    chapters: list[Chapter] = []
    if starts:
        if starts[0][0] > 0:
            starts.insert(0, (0, "Front matter"))
        for k, (p0, t) in enumerate(starts):
            p1 = starts[k + 1][0] if k + 1 < len(starts) else len(page_paras)
            chapters.append(Chapter(t, _merge(page_paras, p0, p1)))
    else:
        n = len(page_paras)
        allp = _merge(page_paras, 0, n)
        heads = {id(p) for p in allp if len(p.text) <= 70 and CHAPTER_RE.match(p.text)}
        if len(heads) >= 2:
            cur = Chapter("Front matter")
            chapters.append(cur)
            for para in allp:
                if id(para) in heads:
                    para.kind = "h"
                    cur = Chapter(para.text)
                    chapters.append(cur)
                cur.paragraphs.append(para)
        else:
            chunk = 15
            for p0 in range(0, n, chunk):
                p1 = min(p0 + chunk, n)
                chapters.append(Chapter(f"Pages {p0 + 1}–{p1}", _merge(page_paras, p0, p1)))
    book = Book(path=path, fmt="pdf", title=title, author=author, chapters=[c for c in chapters if c.paragraphs])
    book.ocr_pages_needed = needed  # type: ignore[attr-defined]
    return book


def _merge(page_paras: list[list[str]], p0: int, p1: int) -> list[Paragraph]:
    """Join paragraphs that continue across block / page breaks."""
    out: list[Paragraph] = []
    for pi in range(p0, p1):
        for t in page_paras[pi]:
            if out and not _END.search(out[-1].text) and t[:1].islower():
                out[-1].text = out[-1].text + " " + t
            else:
                out.append(Paragraph(text=t, page=None))
    return out


def pdf_meta(path: str) -> tuple[str, str, bytes | None]:
    doc = _open(path)
    try:
        meta = doc.metadata or {}
        ft, fa = title_author_from_name(path)
        cover = None
        if doc.page_count:
            import pymupdf as fitz
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False)
            cover = pix.tobytes("jpeg")
        return clean_ws(meta.get("title") or "") or ft, clean_ws(meta.get("author") or "") or fa, cover
    finally:
        doc.close()
