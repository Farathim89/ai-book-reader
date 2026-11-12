"""ABS-style wooden bookshelf: covers standing on planks over a wood-panel wall.

One widget for books, series (stacked covers) and authors. Only visible rows are painted, so it
stays smooth with a thousand covers. The wood is generated procedurally (no images copied)."""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip

from . import theme
from .widgets import paint_shape


@dataclass
class ShelfItem:
    kind: str                       # "book" | "series" | "author"
    key: object                     # book id / series name / author name
    title: str
    subtitle: str = ""
    covers: list = field(default_factory=list)   # cover image paths (series: first volumes)
    progress: float = 0.0
    finished: bool = False
    badge: str = ""                 # top-left pill, e.g. "#3"
    count: int = 0                  # series / author: number of books
    data: dict | None = None


# ------------------------------------------------------------------------------------------ wood
_WOOD: dict[tuple, QPixmap] = {}


def _wood(w: int, h: int, base: tuple[int, int, int], horizontal: bool, seed: int) -> QPixmap:
    key = (w, h, base, horizontal, seed)
    if key in _WOOD:
        return _WOOD[key]
    rng = np.random.default_rng(seed)
    n, length = (h, w) if horizontal else (w, h)      # grain runs along "length"
    across = np.arange(n, dtype=np.float32)
    profile = (np.sin(across * 0.55 + rng.random() * 6) * 0.25 + np.sin(across * 0.13 + rng.random() * 6) * 0.35
               + np.cumsum(rng.normal(0, 1, n)) * 0.03)
    profile += rng.normal(0, 0.18, n)
    t = np.arange(length, dtype=np.float32)
    warp = np.sin(t / 61.0 + rng.random() * 6) * 2.2 + np.sin(t / 17.0 + rng.random() * 6) * 0.6
    idx = (across[:, None] + warp[None, :]).astype(np.int32) % n
    v = profile[idx]
    v = (v - v.min()) / (np.ptp(v) + 1e-6)
    v = v * 0.55 + rng.normal(0, 0.035, v.shape)
    if not horizontal:                                  # board seams on the wall
        seam = np.ones(n, np.float32)
        board = max(90, n // 4)
        for x in range(0, n, board):
            seam[x:x + 2] = 0.45
            seam[x + 2:x + 4] = 1.12
        v = v * seam[:, None]
    shade = 0.62 + 0.55 * np.clip(v, 0, 1.2)
    img = np.clip(np.array(base, np.float32)[None, None, :] * shade[..., None], 0, 255).astype(np.uint8)
    if not horizontal:
        img = img.transpose(1, 0, 2)                    # (h, w, 3)
    img = np.ascontiguousarray(img)
    qimg = QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 3, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    _WOOD[key] = pm
    return pm


WALL = (78, 52, 33)
PLANK = (130, 89, 53)

# name: (texture, wall colour, plank colour)   - "Plain" uses the colour theme instead
SHELF_STYLES: dict[str, tuple] = {
    "Walnut": ("wood", (78, 52, 33), (130, 89, 53)),
    "Oak": ("wood", (148, 108, 66), (192, 146, 94)),
    "Dark Oak": ("wood", (60, 40, 23), (104, 72, 42)),
    "Cherry": ("wood", (98, 42, 28), (156, 76, 50)),
    "Ebony": ("wood", (36, 27, 22), (74, 55, 42)),
    "Stone": ("stone", (86, 86, 90), (128, 126, 122)),
    "Plain": ("plain", None, None),
}
SHELF_STYLE = "Walnut"
SHOW_TITLES = False                 # kept for old callers: True = titles on the cover
TITLE_MODES = {"off": "Off", "cover": "On the cover", "under": "Under the book (label on the shelf)"}
TITLE_MODE = "under"                # "off" | "cover" | "under"


def set_shelf_style(name: str) -> None:
    global SHELF_STYLE
    SHELF_STYLE = name if name in SHELF_STYLES else "Walnut"


def _stone(w: int, h: int, base: tuple[int, int, int], slab: bool, seed: int) -> QPixmap:
    key = ("stone", w, h, base, slab, seed)
    if key in _WOOD:
        return _WOOD[key]
    rng = np.random.default_rng(seed)
    # soft mottling: blurred noise at two scales
    coarse = rng.normal(0, 1, (h // 16 + 2, w // 16 + 2)).astype(np.float32)
    coarse = np.kron(coarse, np.ones((16, 16), np.float32))[:h, :w]
    k = np.ones(9, np.float32) / 9
    coarse = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 1, coarse)
    coarse = np.apply_along_axis(lambda c: np.convolve(c, k, "same"), 0, coarse)
    v = 0.85 + coarse * 0.06 + rng.normal(0, 0.035, (h, w))
    if not slab:   # blocks with darker grout
        bh, bw = 64, 128
        for y in range(0, h, bh):
            v[y:y + 3, :] *= 0.55
            off = (y // bh) % 2 * (bw // 2)
            for x in range(off, w + bw, bw):
                v[y:y + bh, max(0, x - 3):min(w, x)] *= 0.55
            v[y:y + bh, :] *= 0.92 + rng.random() * 0.12
    img = np.clip(np.array(base, np.float32)[None, None, :] * v[..., None], 0, 255).astype(np.uint8)
    img = np.ascontiguousarray(img)
    qimg = QImage(img.data, w, h, w * 3, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    _WOOD[key] = pm
    return pm


def _plain(w: int, h: int, color: str, slab: bool) -> QPixmap:
    key = ("plain", w, h, color, slab)
    if key not in _WOOD:
        pm = QPixmap(w, h)
        c = QColor(color)
        pm.fill(c.lighter(135) if slab else c.darker(118))
        _WOOD[key] = pm
    return _WOOD[key]


def _at_scale(pm: QPixmap, dpr: float) -> QPixmap:
    """The texture pre-stretched to the screen's scale (e.g. 250%), so painting it is a plain copy -
    stretching it on every repaint was a big part of the cost of resizing the window."""
    if abs(dpr - 1.0) < 0.01:
        return pm
    key = ("scaled", pm.cacheKey(), round(dpr, 2))
    if key not in _WOOD:
        img = pm.toImage().scaled(round(pm.width() * dpr), round(pm.height() * dpr), Qt.IgnoreAspectRatio,
                                  Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
        out = QPixmap.fromImage(img)
        out.setDevicePixelRatio(dpr)
        _WOOD[key] = out
    return _WOOD[key]


def wall_pixmap(style: str | None = None, dpr: float = 1.0) -> QPixmap:
    kind, wall, _ = SHELF_STYLES[style if style in SHELF_STYLES else SHELF_STYLE]
    if kind == "stone":
        return _at_scale(_stone(512, 512, wall, False, 5), dpr)
    if kind == "plain":
        return _at_scale(_plain(512, 512, theme.BG, False), dpr)
    return _at_scale(_wood(512, 512, wall, False, 7), dpr)


def plank_pixmap(style: str | None = None, dpr: float = 1.0) -> QPixmap:
    kind, _, plank = SHELF_STYLES[style if style in SHELF_STYLES else SHELF_STYLE]
    if kind == "stone":
        return _at_scale(_stone(512, 64, plank, True, 9), dpr)
    if kind == "plain":
        return _at_scale(_plain(512, 64, theme.SURFACE2, True), dpr)
    return _at_scale(_wood(512, 64, plank, True, 11), dpr)


# ------------------------------------------------------------------------------------------ covers
class CoverCache:
    """Covers scaled and cropped once per size. With `radius` the rounded corners are cut out here too, so
    painting is a plain copy (a clip path + stretch per cover, per repaint, made resizing the window lag)."""

    def __init__(self, budget_mb: int = 320):
        self.budget = budget_mb * 1024 * 1024
        self.used = 0
        self._c: OrderedDict[tuple, QPixmap | None] = OrderedDict()

    def get(self, path: str | None, w: int, h: int, dpr: float = 2.0, radius: float = 0.0) -> QPixmap | None:
        if not path:
            return None
        key = (path, w, h, round(dpr, 2), radius)
        if key in self._c:
            self._c.move_to_end(key)
            return self._c[key]
        img = QImage(path)
        out = None
        if not img.isNull() and w > 0 and h > 0:
            tw, th = max(1, round(w * dpr)), max(1, round(h * dpr))
            img = img.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            img = img.copy((img.width() - tw) // 2, (img.height() - th) // 2, tw, th)
            img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            if radius:
                cut = QImage(tw, th, QImage.Format_ARGB32_Premultiplied)
                cut.fill(Qt.transparent)
                q = QPainter(cut)
                q.setRenderHint(QPainter.Antialiasing)
                q.setPen(Qt.NoPen)
                q.setBrush(QBrush(img))
                q.drawRoundedRect(QRectF(0, 0, tw, th), radius * dpr, radius * dpr)
                q.end()
                img = cut
            out = QPixmap.fromImage(img)
            out.setDevicePixelRatio(dpr)
            self.used += tw * th * 4
        self._c[key] = out
        while self.used > self.budget and len(self._c) > 1:
            _, old = self._c.popitem(last=False)
            if old is not None:
                self.used -= old.width() * old.height() * 4
        return out

    def drop(self, path: str) -> None:
        for k in [k for k in self._c if k[0] == path]:
            old = self._c.pop(k)
            if old is not None:
                self.used -= old.width() * old.height() * 4


COVERS = CoverCache()


def _hue(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:6], 16) % 360


def paint_placeholder(p: QPainter, r: QRectF, title: str, subtitle: str = "") -> None:
    hue = _hue(title or "?")
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor.fromHsl(hue, 70, 62))
    g.setColorAt(1, QColor.fromHsl((hue + 30) % 360, 90, 28))
    p.fillRect(r, QBrush(g))
    p.setPen(QColor(255, 255, 255, 235))
    f = QFont("Georgia")
    f.setPixelSize(max(11, int(r.width() / 8.5)))
    f.setBold(True)
    p.setFont(f)
    pad = r.width() * 0.09
    p.drawText(r.adjusted(pad, pad * 1.6, -pad, -r.height() * 0.3), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, title)
    if subtitle:
        f2 = QFont("Segoe UI")
        f2.setPixelSize(max(9, int(r.width() / 12)))
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(r.adjusted(pad, 0, -pad, -pad), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignBottom, subtitle)


_SHADOWS: dict[tuple, tuple[QPixmap, QPixmap]] = {}


def _shadow_strips(w: int, h: int, dpr: float) -> tuple[QPixmap, QPixmap]:
    """The cover shadow as two ready-made strips: right of the cover and below it (the cover hides the rest).
    Three see-through layers per cover, drawn on every repaint, were a large part of the resize cost."""
    key = (w, h, round(dpr, 2))
    if key not in _SHADOWS:
        strips = []
        for sw, sh, rects in ((6, h + 2, lambda k: QRectF(0, k, k, h - k * 0.7)),
                              (w + 6, 2, lambda k: QRectF(k * 0.5, 0, w - k * 0.5 + k, k * 0.3))):
            pm = QPixmap(max(1, round(sw * dpr)), max(1, round(sh * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            for k, a in ((6, 40), (4, 60), (2, 80)):
                q.fillRect(rects(k), QColor(0, 0, 0, a))
            q.end()
            strips.append(pm)
        _SHADOWS[key] = (strips[0], strips[1])
    return _SHADOWS[key]


def _dpr(p: QPainter) -> float:
    d = p.device()
    return d.devicePixelRatioF() if d is not None else 1.0


def paint_cover(p: QPainter, r: QRectF, path: str | None, title: str, subtitle: str = "", radius: float = 3) -> None:
    pm = COVERS.get(path, round(r.width()), round(r.height()), _dpr(p), radius)
    if pm:
        p.drawPixmap(r.topLeft(), pm)                 # already the right size, corners already rounded
        return
    clip = QPainterPath()
    clip.addRoundedRect(r, radius, radius)
    p.save()
    p.setClipPath(clip)
    if pm:
        p.drawPixmap(r.toRect(), pm)
    else:
        paint_placeholder(p, r, title, subtitle)
    p.restore()


def _pill(p: QPainter, x: float, y: float, text: str, px: int, bg: QColor, fg: QColor, right: bool = False) -> None:
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setBold(True)
    p.setFont(f)
    w = QFontMetrics(f).horizontalAdvance(text) + px
    h = px + 8
    rect = QRectF(x - w if right else x, y, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(rect, h / 2.6, h / 2.6)
    p.setPen(fg)
    p.drawText(rect, Qt.AlignCenter, text)


# ------------------------------------------------------------------------------------------ widget
class Shelf(QAbstractScrollArea):
    activated = Signal(object)            # ShelfItem clicked
    playRequested = Signal(object)        # play button on a book cover
    menuRequested = Signal(object, QPoint)

    def __init__(self, single_row: bool = False, parent=None):
        super().__init__(parent)
        self.single_row = single_row
        self.items: list[ShelfItem] = []
        self.cover_w = 120
        self.title_mode = TITLE_MODE
        self.empty_text = "Nothing here yet"
        self._hover = -1
        self._hover_play = False
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QAbstractScrollArea.NoFrame)
        if single_row:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            from PySide6.QtWidgets import QToolButton
            self._arrows = []
            for d, glyph in ((-1, "left"), (1, "right")):
                b = QToolButton(self)
                b.setText(theme.I[glyph])
                b.setCursor(Qt.PointingHandCursor)
                b.setFixedSize(40, 40)
                b.setStyleSheet(f'QToolButton {{ font-family: "{theme.ICON_FONT}"; font-size: 16px; color: white; '
                                'background: rgba(0,0,0,0.62); border-radius: 20px; border: none; }'
                                'QToolButton:hover { background: rgba(0,0,0,0.85); }')
                b.clicked.connect(lambda _=False, d=d: self._page(d))
                b.hide()
                self._arrows.append(b)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setSingleStep(60)

    # ---- geometry -------------------------------------------------------------
    @property
    def cover_h(self) -> int:
        return int(self.cover_w * 1.5)

    @property
    def gap(self) -> int:
        return max(22, int(self.cover_w * 0.22))

    @property
    def show_titles(self) -> bool:            # old name: titles drawn on the cover
        return self.title_mode == "cover"

    @show_titles.setter
    def show_titles(self, on: bool) -> None:
        self.set_title_mode("cover" if on else "off")

    def set_title_mode(self, mode: str) -> None:
        self.title_mode = mode if mode in TITLE_MODES else "off"
        self.set_cover_width(self.cover_w)             # the shelf edge grows / shrinks with the label

    def _label_font(self) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(max(10, int(self.cover_w / 11)))
        return f

    @property
    def label_h(self) -> int:
        """Height of the title label under a book ("under" mode): two lines of text."""
        return QFontMetrics(self._label_font()).height() * 2 + 10

    @property
    def plank_h(self) -> int:
        base = max(16, min(30, int(self.cover_w * 0.17)))
        return max(base, self.label_h + 12) if self.title_mode == "under" else base

    @property
    def top_pad(self) -> int:
        return max(24, int(self.cover_w * 0.2))

    @property
    def row_h(self) -> int:
        return self.top_pad + self.cover_h + self.plank_h + 10

    def cols(self) -> int:
        if self.single_row:
            return max(1, len(self.items))
        avail = self.viewport().width() - 2 * 30
        return max(1, (avail + self.gap) // (self.cover_w + self.gap))

    def x0(self) -> int:
        if self.single_row:
            return 30
        c = self.cols()
        total = c * self.cover_w + (c - 1) * self.gap
        return max(20, (self.viewport().width() - total) // 2)

    def rows(self) -> int:
        return 1 if self.single_row else -(-len(self.items) // self.cols()) if self.items else 0

    def fixed_height(self) -> int:
        """Height for a single-row shelf (Home)."""
        return self.row_h + 14

    def item_rect(self, i: int) -> QRectF:
        c = self.cols()
        row, col = (0, i) if self.single_row else divmod(i, c)
        x = self.x0() + col * (self.cover_w + self.gap) - self.horizontalScrollBar().value()
        y = row * self.row_h + self.top_pad - self.verticalScrollBar().value()
        return QRectF(x, y, self.cover_w, self.cover_h)

    def _update_scroll(self) -> None:
        if self.single_row:
            content = self.x0() * 2 + len(self.items) * (self.cover_w + self.gap)
            self.horizontalScrollBar().setRange(0, max(0, content - self.viewport().width()))
            self.horizontalScrollBar().setPageStep(self.viewport().width())
        else:
            content = self.rows() * self.row_h + 20
            self.verticalScrollBar().setRange(0, max(0, content - self.viewport().height()))
            self.verticalScrollBar().setPageStep(self.viewport().height())

    # ---- data -----------------------------------------------------------------
    def set_items(self, items: list[ShelfItem], keep_scroll: bool = False) -> None:
        self.items = items
        self._hover = -1
        if not keep_scroll:
            self.verticalScrollBar().setValue(0)
            self.horizontalScrollBar().setValue(0)
        self._update_scroll()
        self.viewport().update()

    def set_cover_width(self, w: int) -> None:
        self.cover_w = max(70, min(260, int(w)))
        self._update_scroll()
        if self.single_row:
            self.setFixedHeight(self.fixed_height())
            self._place_arrows()
        self.viewport().update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._update_scroll()
        self._place_arrows()

    # ---- row arrows (Home) ------------------------------------------------------
    def _page(self, d: int) -> None:
        sb = self.horizontalScrollBar()
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(260)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(sb.value())
        anim.setEndValue(max(0, min(sb.maximum(), sb.value() + d * int(self.viewport().width() * 0.8))))
        anim.start()
        self._anim = anim

    def _place_arrows(self) -> None:
        if not self.single_row:
            return
        y = self.top_pad + self.cover_h // 2 - 20
        self._arrows[0].move(8, y)
        self._arrows[1].move(self.width() - 48, y)
        self._show_arrows(self.underMouse())

    def _show_arrows(self, show: bool) -> None:
        if not self.single_row:
            return
        sb = self.horizontalScrollBar()
        self._arrows[0].setVisible(show and sb.value() > 0)
        self._arrows[1].setVisible(show and sb.value() < sb.maximum())

    def enterEvent(self, e) -> None:
        self._show_arrows(True)
        super().enterEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(800, self.fixed_height() if self.single_row else 600)

    # ---- paint ----------------------------------------------------------------
    def paintEvent(self, e) -> None:
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        vw, vh = self.viewport().width(), self.viewport().height()
        sx, sy = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        dpr = _dpr(p)
        wall = wall_pixmap(dpr=dpr)
        p.drawTiledPixmap(QRect(0, 0, vw, vh), wall, QPoint(sx % 512, sy % 512))
        plank = plank_pixmap(dpr=dpr)
        if not self.items:
            self._paint_planks(p, vw, vh, sy, plank, max(1, vh // self.row_h + 1))
            p.setPen(QColor(255, 255, 255, 200))
            f = QFont("Segoe UI")
            f.setPixelSize(16)
            p.setFont(f)
            p.drawText(QRect(0, 0, vw, self.row_h), Qt.AlignCenter, self.empty_text)
            return
        first = 0 if self.single_row else max(0, sy // self.row_h)
        last = 0 if self.single_row else min(self.rows() - 1, (sy + vh) // self.row_h)
        rows_to_draw = range(first, last + 1)
        # empty planks below the last row (like an empty bookcase) for a full wall
        extra = 0 if self.single_row else max(0, (sy + vh) // self.row_h - last)
        self._paint_planks(p, vw, vh, sy, plank, None, rows=list(rows_to_draw) + list(range(last + 1, last + 1 + extra)))
        c = self.cols()
        for row in rows_to_draw:
            start = row * c
            for i in range(start, min(len(self.items), start + c)) if not self.single_row else range(len(self.items)):
                r = self.item_rect(i)
                if r.right() < -10 or r.left() > vw + 10:
                    continue
                self._paint_item(p, i, r)

    def _paint_planks(self, p: QPainter, vw: int, vh: int, sy: int, plank: QPixmap, n: int | None, rows=None) -> None:
        rows = rows if rows is not None else range(n or 0)
        for row in rows:
            top = row * self.row_h + self.top_pad + self.cover_h - 3 - sy
            rect = QRect(0, top, vw, self.plank_h)
            p.drawTiledPixmap(rect, plank, QPoint(0, (row * 13) % 40))
            p.fillRect(QRect(0, top, vw, 2), QColor(255, 235, 200, 60))           # front edge highlight
            g = QLinearGradient(0, top + self.plank_h, 0, top + self.plank_h + 14)    # shadow under the plank
            g.setColorAt(0, QColor(0, 0, 0, 150))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(QRect(0, top + self.plank_h, vw, 14), QBrush(g))
            p.fillRect(QRect(0, top + self.plank_h - 3, vw, 3), QColor(0, 0, 0, 70))

    def _paint_item(self, p: QPainter, i: int, r: QRectF) -> None:
        it = self.items[i]
        hovered = i == self._hover
        # shadow behind the cover: only the strips that show right of it and below it (it hides the rest)
        right, below = _shadow_strips(round(r.width()), round(r.height()), _dpr(p))
        p.drawPixmap(QPointF(r.right(), r.top()), right)
        p.drawPixmap(QPointF(r.left(), r.bottom()), below)
        if it.kind == "series":
            self._paint_series(p, it, r)
        elif it.kind == "author":
            self._paint_author(p, it, r)
        else:
            paint_cover(p, r, it.covers[0] if it.covers else None, it.title, it.subtitle)
            if it.progress > 0 or it.finished:
                bar = QRectF(r.left(), r.bottom() - 5, r.width(), 5)
                p.fillRect(bar, QColor(0, 0, 0, 150))
                frac = 1.0 if it.finished else min(1.0, it.progress)
                p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()),
                           QColor(theme.GREEN_HOVER if it.finished else theme.ORANGE))
            if self.title_mode == "cover":
                self._paint_title_strip(p, it, r)
        if self.title_mode == "under":
            sub = it.subtitle if it.kind == "book" else f"{it.count} book{'s' if it.count != 1 else ''}"
            self._paint_label(p, r, it.title, sub, hovered)
        if it.badge:
            _pill(p, r.left() + 5, r.top() + 5, it.badge, max(10, int(self.cover_w / 11)),
                  QColor(0, 0, 0, 175), QColor(255, 255, 255))
        if it.count and it.kind == "series":
            _pill(p, r.right() - 5, r.top() + 5, str(it.count), max(10, int(self.cover_w / 11)),
                  QColor(theme.YELLOW), QColor(20, 20, 20), right=True)
        if hovered:
            p.fillRect(r, QColor(0, 0, 0, 70))
            p.setPen(QPen(QColor(theme.YELLOW), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 4, 4)
            if it.kind == "book":
                pr = self._play_rect(r)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.GREEN_HOVER if self._hover_play else theme.GREEN))
                p.drawEllipse(pr)
                d = pr.width() * 0.52
                paint_shape(p, "play", pr.center().x() - d / 2, pr.center().y() - d / 2, d, "white")

    def _play_rect(self, r: QRectF) -> QRectF:
        d = max(34, min(60, r.width() * 0.36))
        return QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d)

    def label_rect(self, r: QRectF) -> QRectF:
        """The label plate on the shelf edge under a cover."""
        top = r.bottom() - 3 + (self.plank_h - self.label_h) / 2
        return QRectF(r.left(), top, r.width(), self.label_h)

    _LABELS: OrderedDict = OrderedDict()           # rendered label plates, shared by all shelves

    def _paint_label(self, p: QPainter, r: QRectF, title: str, sub: str, hovered: bool) -> None:
        plate = self.label_rect(r)
        dpr = _dpr(p)
        key = (title, sub, round(plate.width()), round(plate.height()), hovered, self.cover_w, round(dpr, 2),
               theme.YELLOW, theme.MUTED)
        pm = self._LABELS.get(key)
        if pm is None:
            pm = QPixmap(round((plate.width() + 2) * dpr), round((plate.height() + 2) * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            self._draw_label(q, QRectF(1, 1, plate.width(), plate.height()), title, sub, hovered)
            q.end()
            self._LABELS[key] = pm
            while len(self._LABELS) > 600:
                self._LABELS.popitem(last=False)
        else:
            self._LABELS.move_to_end(key)
        p.drawPixmap(plate.topLeft() - QPointF(1, 1), pm)

    def _draw_label(self, p: QPainter, plate: QRectF, title: str, sub: str, hovered: bool) -> None:
        path = QPainterPath()
        path.addRoundedRect(plate, 4, 4)
        p.fillPath(path, QColor(0, 0, 0, 150 if not hovered else 190))
        p.setPen(QPen(QColor(theme.YELLOW) if hovered else QColor(255, 235, 200, 45), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        f = self._label_font()
        fm = QFontMetrics(f)
        inner = plate.adjusted(6, 5, -6, -5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.YELLOW) if hovered else QColor("white"))
        p.drawText(inner, Qt.AlignHCenter | Qt.AlignTop,
                   QFontMetrics(f).elidedText(title or "", Qt.ElideRight, int(inner.width())))
        if sub:
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(theme.MUTED))
            p.drawText(inner, Qt.AlignHCenter | Qt.AlignBottom, fm.elidedText(sub, Qt.ElideRight, int(inner.width())))

    def _paint_title_strip(self, p: QPainter, it: ShelfItem, r: QRectF) -> None:
        f = QFont("Segoe UI")
        f.setPixelSize(max(10, int(self.cover_w / 11)))
        f.setBold(True)
        fm = QFontMetrics(f)
        lines = 2 if it.subtitle else 1
        h = fm.height() * lines + 22
        strip = QRectF(r.left(), r.bottom() - h, r.width(), h)
        g = QLinearGradient(strip.topLeft(), strip.bottomLeft())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(0.3, QColor(0, 0, 0, 185))
        g.setColorAt(1, QColor(0, 0, 0, 230))
        p.fillRect(strip, QBrush(g))
        p.setFont(f)
        p.setPen(QColor("white"))
        text_r = QRectF(strip.left() + 6, strip.bottom() - fm.height() * lines - 9, strip.width() - 12,
                        fm.height() * lines + 4)
        p.drawText(text_r, Qt.AlignLeft | Qt.AlignTop, fm.elidedText(it.title, Qt.ElideRight, int(text_r.width())))
        if it.subtitle:
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(220, 220, 220))
            p.drawText(text_r.adjusted(0, fm.height(), 0, 0), Qt.AlignLeft | Qt.AlignTop,
                       fm.elidedText(it.subtitle, Qt.ElideRight, int(text_r.width())))

    def _paint_series(self, p: QPainter, it: ShelfItem, r: QRectF) -> None:
        covers = it.covers[:3] or [None]
        n = len(covers)
        off = r.width() * 0.09
        front_w = r.width() - off * (n - 1)
        for k in range(n - 1, -1, -1):          # back to front
            cr = QRectF(r.left() + off * k, r.top() + (n - 1 - k) * 0 + 0, front_w, r.height() - off * k * 0.6)
            cr.moveTop(r.top() + off * k * 0.6)
            paint_cover(p, cr, covers[k], it.title)
            if k:
                p.fillRect(cr, QColor(0, 0, 0, 70 + 30 * k))
        if self.title_mode != "under":           # the label under the book shows the name instead
            self._name_strip(p, r, it.title, f"{it.count} book{'s' if it.count != 1 else ''}")

    def _paint_author(self, p: QPainter, it: ShelfItem, r: QRectF) -> None:
        hue = _hue(it.title)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor.fromHsl(hue, 35, 70))
        g.setColorAt(1, QColor.fromHsl(hue, 30, 30))
        path = QPainterPath()
        path.addRoundedRect(r, 3, 3)
        p.fillPath(path, QBrush(g))
        if it.covers:   # faded cover of one of their books in the background
            p.save()
            p.setOpacity(0.28)
            paint_cover(p, r, it.covers[0], it.title)
            p.restore()
        d = r.width() * 0.56
        circle = QRectF(r.center().x() - d / 2, r.top() + r.height() * 0.16, d, d)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawEllipse(circle)
        initials = "".join(w[0] for w in it.title.replace(".", " ").split()[:2] if w[:1].isalpha()).upper() or "?"
        f = QFont("Segoe UI")
        f.setPixelSize(int(d * 0.36))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 230))
        p.drawText(circle, Qt.AlignCenter, initials)
        if self.title_mode != "under":
            self._name_strip(p, r, it.title, f"{it.count} book{'s' if it.count != 1 else ''}")

    def _name_strip(self, p: QPainter, r: QRectF, name: str, sub: str) -> None:
        h = max(38, r.height() * 0.23)
        strip = QRectF(r.left(), r.bottom() - h, r.width(), h)
        p.fillRect(strip, QColor(0, 0, 0, 190))
        f = QFont("Segoe UI")
        f.setPixelSize(max(10, int(self.cover_w / 10.5)))
        f.setBold(True)
        fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(QColor("white"))
        inner = strip.adjusted(6, 5, -6, -4)
        p.drawText(inner, Qt.AlignHCenter | Qt.AlignTop, fm.elidedText(name, Qt.ElideRight, int(inner.width())))
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(inner, Qt.AlignHCenter | Qt.AlignBottom, sub)

    # ---- mouse ----------------------------------------------------------------
    def _index_at(self, pos) -> int:
        for i in self._visible_indexes():
            r = self.item_rect(i)
            if r.contains(pos) or (self.title_mode == "under" and self.label_rect(r).contains(pos)):
                return i
        return -1

    def _visible_indexes(self):
        if not self.items:
            return range(0)
        if self.single_row:
            return range(len(self.items))
        sy, vh = self.verticalScrollBar().value(), self.viewport().height()
        c = self.cols()
        first = max(0, sy // self.row_h) * c
        last = min(len(self.items), ((sy + vh) // self.row_h + 1) * c)
        return range(first, last)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        i = self._index_at(pos)
        play = i >= 0 and self.items[i].kind == "book" and self._play_rect(self.item_rect(i)).contains(pos)
        if i != self._hover or play != self._hover_play:
            self._hover, self._hover_play = i, play
            self.viewport().setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            self.viewport().update()
        if i >= 0:
            it = self.items[i]
            QToolTip.showText(e.globalPosition().toPoint(), f"{it.title}\n{it.subtitle}".strip(), self.viewport())

    def leaveEvent(self, e) -> None:
        self._hover, self._hover_play = -1, False
        self.viewport().update()
        if self.single_row and not self.rect().contains(self.mapFromGlobal(self.cursor().pos())):
            self._show_arrows(False)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        pos = e.position()
        i = self._index_at(pos)
        if i < 0:
            return
        it = self.items[i]
        if e.button() == Qt.LeftButton:
            if it.kind == "book" and self._play_rect(self.item_rect(i)).contains(pos):
                self.playRequested.emit(it)
            else:
                self.activated.emit(it)
        elif e.button() == Qt.RightButton:
            self.menuRequested.emit(it, e.globalPosition().toPoint())

    def wheelEvent(self, e) -> None:
        if self.single_row:
            # the wheel scrolls the page; Shift+wheel (or a sideways wheel/touchpad) scrolls the row
            horizontal = e.angleDelta().x() != 0 or bool(e.modifiers() & Qt.ShiftModifier)
            if horizontal:
                d = e.angleDelta().x() or e.angleDelta().y()
                sb = self.horizontalScrollBar()
                sb.setValue(sb.value() - d)
                e.accept()
            else:
                e.ignore()
            return
        super().wheelEvent(e)

    def scrollContentsBy(self, dx, dy) -> None:
        self.viewport().update()
        if self.single_row:
            self._show_arrows(True)
