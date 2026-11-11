"""Reading text from images: comic pages (speech bubbles with positions) and scanned book pages.

RapidOCR (local ONNX) finds where the text is. When an Ollama vision model is available it
transcribes the page in the right reading order (much better text), and its bubbles are
matched to the RapidOCR boxes for the on-page outline."""
from __future__ import annotations

import difflib
import io
import re
import threading

import numpy as np

from .ollama import Ollama, OllamaError

_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _rapid():
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR
            _ENGINE = RapidOCR()
        return _ENGINE


def _image(img_bytes: bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return im


def detect_lines(img_bytes: bytes) -> tuple[list[tuple[list[float], str]], int, int]:
    """Text lines as ([x0, y0, x1, y1] in pixels, text), plus the image size."""
    im = _image(img_bytes)
    w, h = im.size
    result, _ = _rapid()(np.asarray(im)[:, :, ::-1])   # RapidOCR expects BGR
    lines = []
    for box, text, score in result or []:
        if float(score) < 0.5 or not text.strip():
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append(([min(xs), min(ys), max(xs), max(ys)], text.strip()))
    return lines, w, h


def group_bubbles(lines: list[tuple[list[float], str]]) -> list[tuple[list[float], str]]:
    """Merge text lines that sit in the same balloon (stacked, overlapping horizontally)."""
    n = len(lines)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        bi = lines[i][0]
        hi = bi[3] - bi[1]
        for j in range(i + 1, n):
            bj = lines[j][0]
            hj = bj[3] - bj[1]
            lh = max(1.0, min(hi, hj))
            vgap = max(bj[1] - bi[3], bi[1] - bj[3])
            overlap = min(bi[2], bj[2]) - max(bi[0], bj[0])
            ci, cj = (bi[0] + bi[2]) / 2, (bj[0] + bj[2]) / 2
            close_x = overlap > -0.5 * lh or abs(ci - cj) < 0.6 * max(bi[2] - bi[0], bj[2] - bj[0])
            if vgap < 0.9 * lh and close_x and abs(hi - hj) < 0.8 * max(hi, hj):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idxs in groups.values():
        idxs.sort(key=lambda k: (lines[k][0][1], lines[k][0][0]))
        box = [min(lines[k][0][0] for k in idxs), min(lines[k][0][1] for k in idxs),
               max(lines[k][0][2] for k in idxs), max(lines[k][0][3] for k in idxs)]
        text = ""
        for k in idxs:
            t = lines[k][1]
            if text.endswith("-") and not text.endswith(" -"):
                text = text[:-1] + t
            else:
                text = f"{text} {t}".strip()
        out.append((box, text))
    return out


def reading_order(bubbles: list[tuple[list[float], str]], rtl: bool) -> list[tuple[list[float], str]]:
    """Rows top to bottom (bubbles whose vertical ranges overlap share a row), then x per row."""
    items = sorted(bubbles, key=lambda b: b[0][1])
    rows: list[list] = []
    for b in items:
        if rows:
            row = rows[-1]
            row_bottom = max(x[0][3] for x in row)
            row_top = min(x[0][1] for x in row)
            h = b[0][3] - b[0][1]
            if b[0][1] < row_bottom - 0.3 * h and b[0][1] >= row_top - h:
                row.append(b)
                continue
        rows.append([b])
    out = []
    for row in rows:
        row.sort(key=lambda b: -b[0][0] if rtl else b[0][0])
        out.extend(row)
    return out


def fix_spacing(text: str) -> str:
    """RapidOCR's model often glues English words together ("WHEREARE", "HOME.IT'S")."""
    text = re.sub(r"([.!?,;:])(?=[A-Za-z])", r"\1 ", text)
    try:
        import wordninja
        costs = wordninja.DEFAULT_LANGUAGE_MODEL._wordcost
    except Exception:
        return text
    # contractions glued to the next word: IT'SMADE -> IT'S MADE, YOU'REIN -> YOU'RE IN
    text = re.sub(r"(?i)\b(\w+'(?:s|re|ll|ve|m|d|t))([a-z]{2,})\b",
                  lambda m: f"{m.group(1)} {m.group(2)}" if m.group(2).lower() in costs else m.group(0), text)

    def fix(m: re.Match) -> str:
        w = m.group(0)
        if len(w) < 6 or w.lower() in costs:
            return w
        parts = wordninja.split(w)
        return " ".join(parts) if len(parts) > 1 and all(len(p) > 1 or p.lower() in "ai" for p in parts) else w
    return re.sub(r"[A-Za-z]+", fix, text)


def _is_speech(t: str) -> bool:
    """Drop page numbers and text that is not English (e.g. untranslated sound effects)."""
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 2:
        return False
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) > 0.7


def _looks_like_dialogue(t: str) -> bool:
    if not _is_speech(t):
        return False
    words = re.findall(r"[A-Za-z']+", t)
    return len(words) >= 4 or (len(words) >= 2 and bool(re.search(r"[.!?…]\s*$", t.strip())))


def _key(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def match_boxes(texts: list[str], groups: list[tuple[list[float], str]]) -> list[list[float] | None]:
    """For each transcribed bubble text find the OCR group with the most similar text."""
    used: set[int] = set()
    out: list[list[float] | None] = []
    keys = [_key(g[1]) for g in groups]
    for t in texts:
        kt = _key(t)
        best, best_r = -1, 0.0
        for i, kg in enumerate(keys):
            if i in used or not kg or not kt:
                continue
            r = difflib.SequenceMatcher(None, kt, kg).ratio()
            if r > best_r:
                best, best_r = i, r
        if best >= 0 and best_r >= 0.45:
            used.add(best)
            out.append(groups[best][0])
        else:
            out.append(None)
    return out


COMIC_PROMPT = (
    "This is a comic book page. Every piece of text on it is marked with a red box and a red number.\n"
    "The OCR text found in each box (it can contain mistakes):\n{regions}\n\n"
    "List the boxes that are speech balloons, thought balloons or caption boxes, in the order a reader "
    "reads them: panel by panel, {order}, top to bottom. Leave out boxes that are sound effects, page "
    "numbers, logos or untranslated text. For each box give its number and its correct text, written in "
    "normal sentence case (not ALL CAPS), as one line.\n"
    'Answer with JSON: {{"bubbles": [{{"id": 3, "text": "..."}}, ...]}}'
)
COMIC_SCHEMA = {"type": "object", "properties": {"bubbles": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
    "required": ["id", "text"]}}}, "required": ["bubbles"]}
SCAN_PROMPT = (
    "This is a scanned page of a book. Transcribe all of the body text exactly, paragraph by paragraph. "
    "Skip running headers, page numbers and footnote markers. Join hyphenated line breaks. "
    'Answer with JSON: {"paragraphs": ["...", "..."]}'
)
SCAN_SCHEMA = {"type": "object", "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
               "required": ["paragraphs"]}


def _marked_image(img_bytes: bytes, boxes: list[list[float]], max_side: int = 1600) -> bytes:
    """The page with a red numbered box around every text region."""
    from PIL import ImageDraw, ImageFont
    im = _image(img_bytes)
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)))
    d = ImageDraw.Draw(im)
    size = max(18, int(im.size[1] / 45))
    try:
        font = ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    for k, b in enumerate(boxes):
        x0, y0, x1, y1 = (v * scale for v in b)
        d.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=(230, 0, 0), width=3)
        label = str(k + 1)
        tw = d.textlength(label, font=font)
        lx, ly = max(0, x0 - 4), max(0, y0 - 4 - size - 6)
        d.rectangle([lx, ly, lx + tw + 10, ly + size + 6], fill=(230, 0, 0))
        d.text((lx + 5, ly + 2), label, fill="white", font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _vlm_image(img_bytes: bytes, max_side: int = 1600) -> bytes:
    im = _image(img_bytes)
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


class PageReader:
    def __init__(self, ollama: Ollama | None, vision_model: str | None):
        self.ollama = ollama
        self.model = vision_model
        self.vlm_ok = bool(ollama and vision_model)

    def comic_page(self, img_bytes: bytes, rtl: bool = False) -> list[dict]:
        lines, w, h = detect_lines(img_bytes)
        groups = reading_order(group_bubbles(lines), rtl)
        if not groups:
            return []
        if self.vlm_ok:
            try:
                bubbles = self._vlm_bubbles(img_bytes, groups, rtl)
                if bubbles:
                    return [{"text": t, "box": _norm_box(groups[i][0], w, h)} for i, t in bubbles]
            except OllamaError:
                pass
        # RapidOCR only
        return [{"text": fix_spacing(t), "box": _norm_box(b, w, h)} for b, t in groups if _is_speech(t)]

    def _vlm_bubbles(self, img_bytes: bytes, groups, rtl: bool) -> list[tuple[int, str]]:
        """Set-of-marks prompting: numbered boxes on the page, the model picks order + fixes text."""
        marked = _marked_image(img_bytes, [g[0] for g in groups])
        regions = "\n".join(f"{i + 1}: {fix_spacing(t)}" for i, (_, t) in enumerate(groups))
        order = "right to left (manga)" if rtl else "left to right"
        res = self.ollama.chat_json(self.model, "You read comics aloud for an audiobook narrator.",
                                    COMIC_PROMPT.format(regions=regions, order=order), schema=COMIC_SCHEMA,
                                    images=[marked], num_ctx=8192)
        out: list[tuple[int, str]] = []
        seen: set[int] = set()
        for b in res.get("bubbles", []) or []:
            if not isinstance(b, dict):
                continue
            try:
                i = int(str(b.get("id", "")).strip().lstrip("#")) - 1
            except ValueError:
                continue
            text = " ".join(str(b.get("text", "")).split())
            if 0 <= i < len(groups) and i not in seen and text:
                seen.add(i)
                out.append((i, text))
        if not out:
            return out
        # safety net: the model sometimes drops a real balloon. Put back OCR regions that clearly are
        # sentences (several words or sentence punctuation), at their position in the layout order.
        for k, (_, t) in enumerate(groups):
            if k in seen or not _looks_like_dialogue(t):
                continue
            pos = 0
            for n, (i, _) in enumerate(out):
                if i < k:
                    pos = n + 1
            out.insert(pos, (k, fix_spacing(t)))
            seen.add(k)
        return out

    def scan_page(self, img_bytes: bytes) -> list[str]:
        if self.vlm_ok:
            try:
                res = self.ollama.chat_json(self.model, "You transcribe scanned book pages.", SCAN_PROMPT,
                                            schema=SCAN_SCHEMA, images=[_vlm_image(img_bytes, 2000)],
                                            num_ctx=12288)
                paras = [str(p).strip() for p in res.get("paragraphs", []) if str(p).strip()]
                if paras:
                    return paras
            except OllamaError:
                pass
        lines, w, h = detect_lines(img_bytes)
        return [fix_spacing(p) for p in lines_to_paragraphs(lines)]


def lines_to_paragraphs(lines: list[tuple[list[float], str]]) -> list[str]:
    if not lines:
        return []
    lines = sorted(lines, key=lambda l: (l[0][1], l[0][0]))
    heights = sorted(l[0][3] - l[0][1] for l in lines)
    lh = heights[len(heights) // 2] or 1
    left = sorted(l[0][0] for l in lines)[len(lines) // 10]
    paras: list[str] = []
    prev = None
    for box, text in lines:
        new = prev is None or box[1] - prev[3] > 0.9 * lh or box[0] - left > 1.5 * lh
        if new or not paras:
            paras.append(text)
        elif paras[-1].endswith("-"):
            paras[-1] = paras[-1][:-1] + text
        else:
            paras[-1] += " " + text
        prev = box
    return paras


def _norm_box(b: list[float], w: int, h: int) -> list[float]:
    pad = 0.008
    return [max(0.0, b[0] / w - pad), max(0.0, b[1] / h - pad), min(1.0, b[2] / w + pad), min(1.0, b[3] / h + pad)]
