"""Text helpers: whitespace clean-up, sentence splitting, dialogue (quote) detection,
and text clean-up right before speech synthesis."""
from __future__ import annotations

import re
import unicodedata

from .model import Book, Paragraph, Quote, Sentence

_WS = re.compile(r"\s+")
_SOFT = dict.fromkeys(map(ord, "­​‌‍﻿⁠"), None)


def clean_ws(text: str) -> str:
    """Collapse whitespace, drop soft hyphens / zero-width chars, NFC-normalise."""
    text = unicodedata.normalize("NFC", text.translate(_SOFT))
    return _WS.sub(" ", text).strip()


# --------------------------------------------------------------------------- sentences
ABBREV = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof", "capt", "col", "gen", "lt", "sgt",
    "mt", "vs", "etc", "e.g", "i.e", "no", "nos", "fig", "rev", "hon", "gov", "pres", "sen",
    "rep", "maj", "cmdr", "adm", "ave", "blvd", "rd", "co", "corp", "inc", "ltd", "dept",
    "approx", "vol", "ch", "pp", "ed", "eds", "esp", "al", "viz", "cf", "ft", "oz", "lb",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "messrs", "mme", "mlle", "mssrs", "op", "cit", "ibid", "a.m", "p.m", "u.s", "u.k",
}
# end punctuation, optional closing quotes/brackets, whitespace, then something that
# can start a sentence (capital, digit, opening quote/bracket)
_SPLIT = re.compile(r"([.!?…]+)([\"'”’)\]]*)(\s+)(?=[\"'“‘(\[]?[A-Z0-9À-Þ])")
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of the sentences in ``text`` (no surrounding spaces)."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SPLIT.finditer(text):
        punct = m.group(1)
        if punct == ".":
            wm = _WORD_BEFORE.search(text, 0, m.start())
            if wm:
                word = wm.group(1).lower().rstrip(".")
                if word in ABBREV:
                    continue
                if len(word) == 1 and wm.group(1).isupper():   # initials: "J. R. R. Tolkien"
                    continue
                if re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", wm.group(1)):   # "J.K. Rowling", "U.S.A."
                    continue
        end = m.end(2)
        if end - start > 1:
            spans.append((start, end))
            start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    # trim whitespace, drop empties
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return _merge_tiny(text, out)


def _merge_tiny(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Glue fragments without letters/digits (e.g. a stray '...') onto the previous sentence."""
    out: list[tuple[int, int]] = []
    for s, e in spans:
        frag = text[s:e]
        if out and not any(ch.isalnum() for ch in frag):
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


# --------------------------------------------------------------------------- quotes
OPEN_D, CLOSE_D = "“", "”"
OPEN_S, CLOSE_S = "‘", "’"


def find_quotes(text: str) -> list[tuple[int, int]]:
    """Spans of dialogue in a paragraph, including the quote marks.

    Handles curly double quotes, straight double quotes and (when a paragraph has no
    double quotes at all) British-style single quotes. A quote left open at the end
    of the paragraph runs to the end (multi-paragraph speeches)."""
    spans: list[tuple[int, int]] = []
    if any(c in text for c in (OPEN_D, CLOSE_D, '"')):
        inside, qs = False, 0
        for i, c in enumerate(text):
            if c == OPEN_D or (c == '"' and not inside):
                if not inside:
                    inside, qs = True, i
            elif c == CLOSE_D or (c == '"' and inside):
                if inside:
                    spans.append((qs, i + 1))
                    inside = False
        if inside:
            spans.append((qs, len(text)))
        return spans
    if OPEN_S in text:
        inside, qs = False, 0
        n = len(text)
        for i, c in enumerate(text):
            if c == OPEN_S and not inside and (i == 0 or not text[i - 1].isalnum()):
                inside, qs = True, i
            elif c == CLOSE_S and inside:
                nxt = text[i + 1] if i + 1 < n else " "
                if not nxt.isalpha():           # skip apostrophes: don’t, it’s
                    spans.append((qs, i + 1))
                    inside = False
        if inside:
            spans.append((qs, len(text)))
    return spans


# --------------------------------------------------------------------------- annotate
def annotate_paragraph(p: Paragraph) -> None:
    p.sentences = [Sentence(s, e) for s, e in split_sentences(p.text)]
    if p.kind == "h" or p.box is not None:
        p.quotes = []
    else:
        p.quotes = [Quote(s, e) for s, e in find_quotes(p.text)]


def annotate_book(book: Book) -> None:
    qid = 0
    for ch in book.chapters:
        ch.paragraphs = [p for p in ch.paragraphs if p.text.strip()]
        for p in ch.paragraphs:
            annotate_paragraph(p)
            for q in p.quotes:
                q.qid = qid
                qid += 1
    book.chapters = [c for c in book.chapters if c.paragraphs]


def paragraphs_from_texts(texts, kind: str = "p") -> list[Paragraph]:
    out = []
    for t in texts:
        t = clean_ws(t)
        if t:
            out.append(Paragraph(text=t, kind=kind))
    return out


# --------------------------------------------------------------------------- speech prep
_FOOTNOTE = re.compile(r"\[\d{1,3}\]|(?<=[a-z.,;:!?”\"])\d{1,2}(?=\s|$)|[*†‡]+(?=\s|$)")
_DASHES = re.compile(r"\s*[—―]\s*|\s+-{2,}\s+|-{2,}")
_ELLIPSIS = re.compile(r"…|\.\s\.\s\.")
_REPEAT_PUNCT = re.compile(r"([!?])[!?]+")


def _fix_caps(text: str) -> str:
    """Comics and shouting are often ALL CAPS - TTS would spell words out letter by letter."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return text
    upper = sum(1 for c in letters if c.isupper())
    if upper / len(letters) < 0.8:
        return text
    low = text.lower()
    low = re.sub(r"\bi\b", "I", low)
    low = re.sub(r"(^|[.!?]\s+|[\"“‘]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), low)
    return low


def speech_text(text: str) -> str:
    """Clean a sentence (or a voice run) for the TTS engines."""
    t = _FOOTNOTE.sub("", text)
    t = _ELLIPSIS.sub("...", t)
    t = _DASHES.sub(" - ", t)
    t = _REPEAT_PUNCT.sub(r"\1", t)
    t = _fix_caps(t)
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r'(^|\s)["\']+|["\']+(?=\s|$)', r"\1", t)   # quote marks are silent anyway
    return clean_ws(t)


def has_speech(text: str) -> bool:
    return any(c.isalnum() for c in text)


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Break an over-long sentence at ; : , or spaces so each piece fits the engine."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    pieces: list[str] = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = -1
        for sep in ("; ", ": ", ", ", " - ", " "):
            cut = window.rfind(sep)
            if cut > max_chars // 3:
                cut += len(sep) - 1
                break
        if cut <= 0:
            cut = max_chars
        pieces.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]
