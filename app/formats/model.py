"""Book structure shared by every format.

Book -> chapters -> paragraphs -> sentences. A *unit* is one sentence: the thing
that gets highlighted and spoken. Quotes (dialogue spans) live on paragraphs and
carry the speaker found by the character analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Sentence:
    start: int
    end: int


@dataclass
class Quote:
    start: int
    end: int
    qid: int = -1               # book-wide id, used by the character analysis
    speaker: str | None = None


@dataclass
class Paragraph:
    text: str
    kind: str = "p"             # "p" = body text, "h" = heading
    sentences: list[Sentence] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    page: int | None = None     # page index for comics / scanned pages
    box: list[float] | None = None  # comic bubble box (x0, y0, x1, y1), 0..1 of the page

    def sentence_text(self, i: int) -> str:
        s = self.sentences[i]
        return self.text[s.start:s.end]


@dataclass
class Chapter:
    title: str
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Book:
    path: str
    fmt: str
    title: str
    author: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    kind: str = "text"          # "text" or "comic"
    pages: list[str] = field(default_factory=list)  # comic page names inside the archive
    cover: bytes | None = None  # not serialised
    ocr_pages_needed: list[int] = field(default_factory=list, repr=False)  # scans / comic pages without text yet
    units: list[tuple[int, int, int]] = field(default_factory=list, repr=False)
    chapter_first_unit: list[int] = field(default_factory=list, repr=False)

    # ---- index -------------------------------------------------------------
    def build_index(self) -> None:
        from .textproc import annotate_book
        annotate_book(self)
        self.units = []
        self.chapter_first_unit = []
        for ci, ch in enumerate(self.chapters):
            self.chapter_first_unit.append(len(self.units))
            for pi, para in enumerate(ch.paragraphs):
                for si in range(len(para.sentences)):
                    self.units.append((ci, pi, si))

    def unit_count(self) -> int:
        return len(self.units)

    def unit_text(self, u: int) -> str:
        ci, pi, si = self.units[u]
        return self.chapters[ci].paragraphs[pi].sentence_text(si)

    def unit_para(self, u: int) -> Paragraph:
        ci, pi, _ = self.units[u]
        return self.chapters[ci].paragraphs[pi]

    def chapter_of(self, u: int) -> int:
        return self.units[u][0] if self.units else 0

    def chapter_units(self, ci: int) -> range:
        start = self.chapter_first_unit[ci]
        end = self.chapter_first_unit[ci + 1] if ci + 1 < len(self.chapters) else len(self.units)
        return range(start, end)

    def pause_after(self, u: int) -> str:
        """'sentence', 'paragraph', 'heading' or 'chapter' - how long a break follows unit u."""
        ci, pi, si = self.units[u]
        ch = self.chapters[ci]
        para = ch.paragraphs[pi]
        if si < len(para.sentences) - 1:
            return "sentence"
        if pi == len(ch.paragraphs) - 1 or self.chapter_units(ci).stop - 1 == u:
            return "chapter"
        return "heading" if para.kind == "h" else "paragraph"

    def quote_count(self) -> int:
        return sum(len(p.quotes) for ch in self.chapters for p in ch.paragraphs)

    # ---- (de)serialisation for the parse cache -----------------------------
    def to_dict(self) -> dict:
        return {
            "path": self.path, "fmt": self.fmt, "title": self.title, "author": self.author,
            "kind": self.kind, "pages": self.pages, "ocr_pages_needed": self.ocr_pages_needed,
            "chapters": [
                {"title": ch.title, "paragraphs": [
                    [p.text, p.kind, p.page, p.box] for p in ch.paragraphs]}
                for ch in self.chapters
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Book":
        chapters = [
            Chapter(c["title"], [Paragraph(text=t, kind=k, page=pg, box=bx)
                                 for t, k, pg, bx in c["paragraphs"]])
            for c in d["chapters"]
        ]
        return cls(path=d["path"], fmt=d["fmt"], title=d["title"], author=d.get("author", ""),
                   chapters=chapters, kind=d.get("kind", "text"), pages=d.get("pages", []),
                   ocr_pages_needed=d.get("ocr_pages_needed", []))
