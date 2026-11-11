"""TV mode (F10), like ABS-DA's: a full-screen couch layout with a side menu, big covers and a
Now Playing screen. Works with an Xbox or PlayStation controller, a TV remote's arrow keys or the keyboard;
a dim, moving screen saver comes on while a book plays and nobody presses anything.

Keys: arrows move, Enter/Space = OK, Esc/Backspace = back, F10 = leave TV mode, media keys play.
Controller: D-pad / left stick move, A = OK, B = back, X = play/pause, Y = search, Start = settings,
View = Now Playing, LB/RB = previous/next chapter, LT/RT = back/forward a few sentences,
right stick = volume."""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..library import libraries as L
from ..library import views
from ..paths import resource_dir
from . import theme
from .gamepad import Gamepad
from .player_bar import SLEEP, SPEEDS
from .shelf import paint_cover

COVER_W, COVER_H = 190, 285
CARD_W, CARD_H = COVER_W + 28, COVER_H + 86
MENU = [("home", "home", "Home"), ("library", "library", "Library"), ("series", "series", "Series"),
        ("authors", "author", "Authors"), ("search", "search", "Search"), ("playing", "headphones", "Now Playing"),
        ("settings", "settings", "Settings"), ("exit", "exit", "Leave TV mode")]
SAVER_PLAYING = [(30, "30 seconds"), (60, "1 minute"), (120, "2 minutes"), (300, "5 minutes"), (0, "Off")]
SAVER_IDLE = [(300, "5 minutes"), (600, "10 minutes"), (1800, "30 minutes"), (0, "Off")]


def _font(px: int, bold: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def _size(w: QWidget, px: int, bold: bool = False) -> None:
    """Remember a label's TV text size (applied by _apply_fonts once the page is built)."""
    w.setProperty("tv_px", px)
    w.setProperty("tv_bold", bold)
    w.setFont(_font(px, bold))


def _apply_fonts(root: QWidget) -> None:
    """The app's style sheet sets label sizes and wins over setFont(): put each label's TV size
    into its own style sheet (TV sizes are big on purpose)."""
    for lab in root.findChildren(QLabel):
        px = lab.property("tv_px")
        if px and "font-size" not in lab.styleSheet():
            lab.setStyleSheet(f"{lab.styleSheet()} font-size: {int(px)}px; "
                              f"font-weight: {700 if lab.property('tv_bold') else 400};")


# ------------------------------------------------------------------------------------------ focusables
class TvFocusable(QWidget):
    """Something the highlight can stand on. OK (or a click) calls on_ok."""
    clicked = Signal(object)

    def __init__(self, on_ok=None, parent=None):
        super().__init__(parent)
        self.focused = False
        self.on_ok = on_ok
        self.setCursor(Qt.PointingHandCursor)

    def set_focused(self, on: bool) -> None:
        if self.focused != on:
            self.focused = on
            self.update()

    def activate(self) -> None:
        if self.on_ok:
            self.on_ok()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self)


class TvCard(TvFocusable):
    """A big cover with its title under it (book, series or author)."""

    def __init__(self, title: str, subtitle: str, cover: str | None, on_ok, progress: float = 0.0,
                 finished: bool = False, count: int = 0, kind: str = "book"):
        super().__init__(on_ok)
        self.title, self.subtitle, self.cover = title or "", subtitle or "", cover
        self.progress, self.finished, self.count, self.kind = progress, finished, count, kind
        self.setFixedSize(CARD_W, CARD_H)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        g = 10 if self.focused else 0
        r = QRectF((self.width() - COVER_W - g) / 2, 10 - g / 2, COVER_W + g, COVER_H + g)
        if self.kind == "series":
            for k in (2, 1):
                back = r.translated(7 * k, -5 * k)
                paint_cover(p, back, self.cover, self.title)
                p.fillRect(back, QColor(0, 0, 0, 90 + 40 * k))
        for k, a in ((10, 40), (6, 60), (3, 90)):
            p.fillRect(r.adjusted(k * 0.4, k, k * 0.6, k * 0.5), QColor(0, 0, 0, a))
        paint_cover(p, r, self.cover, self.title, self.subtitle, radius=5)
        if self.progress > 0 or self.finished:
            bar = QRectF(r.left(), r.bottom() - 7, r.width(), 7)
            p.fillRect(bar, QColor(0, 0, 0, 160))
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * (1.0 if self.finished else min(1, self.progress)),
                              bar.height()), QColor(theme.GREEN_HOVER if self.finished else theme.ORANGE))
        if self.count and self.kind == "series":
            f = _font(16, True)
            p.setFont(f)
            t = str(self.count)
            w = QFontMetrics(f).horizontalAdvance(t) + 18
            pill = QRectF(r.right() - w - 6, r.top() + 6, w, 28)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.YELLOW))
            p.drawRoundedRect(pill, 14, 14)
            p.setPen(QColor(20, 20, 20))
            p.drawText(pill, Qt.AlignCenter, t)
        if self.focused:
            p.setPen(QPen(QColor("white"), 4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-4, -4, 4, 4), 8, 8)
        text = QRectF(4, r.bottom() + 12, self.width() - 8, 60)
        f = _font(17, True)
        p.setFont(f)
        p.setPen(QColor("white") if self.focused else QColor(theme.TEXT))
        p.drawText(text, Qt.AlignHCenter | Qt.AlignTop,
                   QFontMetrics(f).elidedText(self.title, Qt.ElideRight, int(text.width())))
        f = _font(14)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(text.adjusted(0, 26, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                   QFontMetrics(f).elidedText(self.subtitle, Qt.ElideRight, int(text.width())))


class TvButton(TvFocusable):
    """A rounded button: white-filled when it has the highlight."""

    def __init__(self, text: str, on_ok=None, glyph: str = "", width: int = 0, height: int = 60,
                 align=Qt.AlignCenter, px: int = 20):
        super().__init__(on_ok)
        self.text, self.glyph, self.align, self.px = text, glyph, align, px
        self.active = False                     # e.g. the open page in the side menu
        f = _font(px, True)
        w = width or int(QFontMetrics(f).horizontalAdvance(text) + 52 + (px * 1.6 + 6 if glyph else 0))
        self.setFixedSize(w, height)

    def set_text(self, text: str) -> None:
        self.text = text
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.focused:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("white"))
        else:
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.setBrush(QColor(theme.SURFACE2 if not self.active else theme.ACCENT_DIM))
        p.drawRoundedRect(r, r.height() / 2 if self.align == Qt.AlignCenter else 12, r.height() / 2
                          if self.align == Qt.AlignCenter else 12)
        fg = QColor(20, 20, 20) if self.focused else QColor(theme.ACCENT if self.active else theme.TEXT)
        x = r.left() + 22
        if self.glyph:
            p.setFont(theme.icon_font(int(self.px * 1.05)))
            p.setPen(fg)
            gr = QRectF(x, r.top(), self.px * 1.6, r.height())
            p.drawText(gr, Qt.AlignVCenter | Qt.AlignLeft, self.glyph)
            x += self.px * 1.6 + 6
        p.setFont(_font(self.px, True))
        p.setPen(fg)
        tr = QRectF(x if self.align != Qt.AlignCenter or self.glyph else r.left(), r.top(),
                    r.right() - (x if self.align != Qt.AlignCenter or self.glyph else r.left()) - 16, r.height())
        al = Qt.AlignVCenter | (Qt.AlignLeft if (self.align != Qt.AlignCenter or self.glyph) else Qt.AlignHCenter)
        p.drawText(tr, al, QFontMetrics(p.font()).elidedText(self.text, Qt.ElideRight, int(tr.width())))


# ------------------------------------------------------------------------------------------ pages
class TvPage(QWidget):
    """A scrolling page of focusable lines. Up/down moves between lines (to the nearest item),
    left/right along a line; left from the first item goes to the side menu."""
    root = ""                                   # menu key for top-level pages

    def __init__(self, tv: "TvWindow", title: str = ""):
        super().__init__()
        self.tv = tv
        self.lines: list[list[TvFocusable]] = []
        self.line_scroll: dict[int, QScrollArea] = {}
        self.pos = (0, 0)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: transparent;")
        outer.addWidget(self.scroll)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(48, 36, 48, 60)
        self.body.setSpacing(12)
        self.scroll.setWidget(inner)
        if title:
            self.heading(title, 34)

    # ---- building
    def heading(self, text: str, px: int = 26) -> QLabel:
        lab = QLabel(text)
        _size(lab, px, True)
        lab.setStyleSheet(f"color: {theme.TEXT}; background: transparent; padding-top: 8px;")
        self.body.addWidget(lab)
        return lab

    def note(self, text: str, px: int = 18) -> QLabel:
        lab = QLabel(text)
        _size(lab, px)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {theme.MUTED}; background: transparent;")
        self.body.addWidget(lab)
        return lab

    def _wire(self, items: list[TvFocusable]) -> None:
        for it in items:
            it.clicked.connect(self._clicked)

    def add_row(self, items: list[TvFocusable]) -> None:
        """One line that scrolls sideways (Home shelves)."""
        if not items:
            return
        area = QScrollArea()
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setStyleSheet("background: transparent;")
        strip = QWidget()
        strip.setStyleSheet("background: transparent;")
        h = QHBoxLayout(strip)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(20)
        for it in items:
            h.addWidget(it)
        h.addStretch(1)
        area.setWidget(strip)
        area.setFixedHeight(items[0].height() + 12)
        self.body.addWidget(area)
        self.line_scroll[len(self.lines)] = area
        self.lines.append(items)
        self._wire(items)

    def add_grid(self, items: list[TvFocusable], cols: int | None = None) -> None:
        if not items:
            return
        cols = cols or self.tv.grid_cols(items[0].width())
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        grid = QGridLayout(box)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(18)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for n, it in enumerate(items):
            grid.addWidget(it, n // cols, n % cols)
        self.body.addWidget(box)
        for k in range(0, len(items), cols):
            self.lines.append(items[k:k + cols])
        self._wire(items)

    def add_buttons(self, items: list[TvFocusable], vertical: bool = False) -> None:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(box) if vertical else QHBoxLayout(box)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(12)
        for it in items:
            lay.addWidget(it, 0, Qt.AlignLeft)
        if not vertical:
            lay.addStretch(1)
        self.body.addWidget(box)
        if vertical:
            self.lines.extend([[it] for it in items])
        else:
            self.lines.append(items)
        self._wire(items)

    def finish(self) -> None:
        self.body.addStretch(1)

    # ---- focus
    def current(self) -> TvFocusable | None:
        li, ci = self.pos
        if 0 <= li < len(self.lines) and 0 <= ci < len(self.lines[li]):
            return self.lines[li][ci]
        return None

    def set_pos(self, li: int, ci: int) -> None:
        old = self.current()
        if old:
            old.set_focused(False)
        if not self.lines:
            return
        li = max(0, min(li, len(self.lines) - 1))
        ci = max(0, min(ci, len(self.lines[li]) - 1))
        self.pos = (li, ci)
        it = self.lines[li][ci]
        it.set_focused(self.tv.zone == "content")
        if li in self.line_scroll:
            self.line_scroll[li].ensureWidgetVisible(it, 60, 0)
            self.scroll.ensureWidgetVisible(self.line_scroll[li], 0, 70)
        else:
            self.scroll.ensureWidgetVisible(it, 0, 90)
        if li == 0:
            self.scroll.verticalScrollBar().setValue(0)

    def focus_in(self) -> None:
        self.set_pos(*self.pos)

    def focus_out(self) -> None:
        it = self.current()
        if it:
            it.set_focused(False)

    def _nearest(self, line: list[TvFocusable], x: int) -> int:
        best, idx = None, 0
        for k, it in enumerate(line):
            cx = it.mapTo(self, it.rect().center()).x()
            if best is None or abs(cx - x) < best:
                best, idx = abs(cx - x), k
        return idx

    def handle(self, action: str) -> str | None:
        """Move or activate. Returns "menu" when the highlight should go to the side menu."""
        if not self.lines:
            return "menu" if action == "left" else None
        li, ci = self.pos
        cur = self.current()
        if action in ("up", "down"):
            nli = li + (-1 if action == "up" else 1)
            if 0 <= nli < len(self.lines):
                x = cur.mapTo(self, cur.rect().center()).x() if cur else 0
                self.set_pos(nli, self._nearest(self.lines[nli], x))
        elif action == "left":
            if ci == 0:
                return "menu"
            self.set_pos(li, ci - 1)
        elif action == "right":
            self.set_pos(li, ci + 1)
        elif action == "ok" and cur:
            cur.activate()
        return None

    def _clicked(self, it: TvFocusable) -> None:
        for li, line in enumerate(self.lines):
            if it in line:
                self.tv.zone = "content"
                self.tv.menu_focus(False)
                self.set_pos(li, line.index(it))
                it.activate()
                return

    def refresh(self) -> None:
        """Pages that show live state (Now Playing) redraw here."""


# ------------------------------------------------------------------------------------------ window
class TvWindow(QWidget):
    exited = Signal()

    def __init__(self, main):
        super().__init__(None, Qt.Window)
        self.main = main
        self.setObjectName("TvWindow")
        self.setWindowTitle("Ai Book Reader – TV mode")
        icon = resource_dir() / "assets" / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setStyleSheet(f"QWidget#TvWindow {{ background: {theme.BG}; }} QLabel {{ color: {theme.TEXT}; }}")
        self.zone = "content"
        self.history: list[TvPage] = []
        self._last_input = time.monotonic()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QFrame()
        side.setObjectName("TvMenu")
        side.setFixedWidth(300)
        side.setStyleSheet(f"QFrame#TvMenu {{ background: {theme.SIDEBAR}; border-right: 1px solid {theme.BORDER}; }}")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(26, 30, 22, 30)
        sl.setSpacing(10)
        logo = QLabel("Ai Book Reader")
        _size(logo, 24, True)
        logo.setStyleSheet(f"color: {theme.TEXT}; background: transparent; padding-bottom: 18px;")
        sl.addWidget(logo)
        self.menu_items: list[TvButton] = []
        for key, glyph, label in MENU:
            b = TvButton(label, lambda k=key: self.open_root(k), theme.I.get(glyph, ""), width=250, height=58,
                         align=Qt.AlignLeft, px=20)
            b.key = key
            b.clicked.connect(self._menu_clicked)
            if key == "exit":
                sl.addStretch(1)
            sl.addWidget(b)
            self.menu_items.append(b)
        self.menu_pos = 0
        self.clock = QLabel("")
        _size(self.clock, 18)
        self.clock.setStyleSheet(f"color: {theme.MUTED}; background: transparent; padding-top: 14px;")
        sl.addWidget(self.clock)
        lay.addWidget(side)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        lay.addWidget(self.stack, 1)
        self.saver = TvSaver(self)
        self.pad = Gamepad(self)
        self.pad.pressed.connect(self._pad)
        self.pad.volume.connect(self._volume_step)
        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self._tick)
        main.narrator.unitStarted.connect(self._narration_changed)
        main.narrator.stateChanged.connect(self._narration_changed)
        _apply_fonts(self)

    # ---- show / leave
    def start(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.pad.start()
        self.tick.start()
        self._tick()
        self.open_root("playing" if self.main.book is not None and self.main.narrator.state == "playing" else "home")

    def leave(self) -> None:
        self.pad.stop()
        self.tick.stop()
        self.saver.hide()
        try:
            self.main.narrator.unitStarted.disconnect(self._narration_changed)
            self.main.narrator.stateChanged.disconnect(self._narration_changed)
        except (RuntimeError, TypeError):
            pass
        self.hide()
        self.exited.emit()
        self.deleteLater()

    def closeEvent(self, e) -> None:          # Alt+F4: back to the normal window
        e.ignore()
        QTimer.singleShot(0, self.leave)

    # ---- helpers for pages
    def rows(self) -> list[dict]:
        return views.in_library(self.main.rows(), self.main.settings.get("current_library"))

    def grid_cols(self, item_w: int) -> int:
        avail = (self.width() or self.screen().size().width()) - 300 - 96
        return max(1, (avail + 20) // (item_w + 20))

    def book_card(self, r: dict) -> TvCard:
        return TvCard(r["title"] or "", r.get("author") or "", r.get("cover"),
                      lambda r=r: self.show_page(BookPage(self, r["id"])),
                      progress=r.get("progress") or 0.0, finished=bool(r.get("finished")))

    # ---- pages
    def open_root(self, key: str) -> None:
        if key == "exit":
            self.leave()
            return
        page = {"home": HomePage, "library": LibraryPage, "series": SeriesPage, "authors": AuthorsPage,
                "search": SearchPage, "playing": NowPlayingPage, "settings": SettingsPage}[key](self)
        page.root = key
        for old in self.history + [self.stack.currentWidget()]:
            if old is not None and old is not page:
                self.stack.removeWidget(old)
                old.deleteLater()
        self.history = []
        _apply_fonts(page)
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        for k, b in enumerate(self.menu_items):
            b.active = b.key == key
            b.update()
            if b.key == key:
                self.menu_pos = k
        self.zone = "content"
        self.menu_focus(False)
        page.set_pos(*page.pos)

    def show_page(self, page: TvPage) -> None:
        cur = self.stack.currentWidget()
        if cur is not None:
            cur.focus_out()
            self.history.append(cur)
        _apply_fonts(page)
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.zone = "content"
        self.menu_focus(False)
        page.set_pos(*page.pos)

    def back(self) -> None:
        if self.zone == "menu":
            return
        if not self.history:
            self.zone = "menu"
            self.page().focus_out()
            self.menu_focus(True)
            return
        cur = self.stack.currentWidget()
        prev = self.history.pop()
        self.stack.setCurrentWidget(prev)
        self.stack.removeWidget(cur)
        cur.deleteLater()
        prev.refresh()
        prev.focus_in()

    def page(self) -> TvPage:
        return self.stack.currentWidget()

    def menu_focus(self, on: bool) -> None:
        for k, b in enumerate(self.menu_items):
            b.set_focused(on and k == self.menu_pos)

    def _menu_clicked(self, b: TvButton) -> None:
        b.activate()

    # ---- input
    def act(self, action: str) -> None:
        self._last_input = time.monotonic()
        if self.saver.isVisible():
            self.saver.hide()
            return
        m = self.main
        if action == "play":
            if m.book is not None:
                m._toggle()
            return
        if action in ("chapter-", "chapter+"):
            if m.book is not None:
                m.narrator.chapter_step(-1 if action == "chapter-" else 1)
            return
        if action in ("skip-", "skip+"):
            if m.book is not None:
                m.narrator.step(-3 if action == "skip-" else 3)
            return
        if action in ("search", "settings", "playing"):
            self.open_root(action)
            return
        if action == "back":
            self.back()
            return
        if self.zone == "menu":
            if action in ("up", "down"):
                self.menu_pos = max(0, min(len(self.menu_items) - 1, self.menu_pos + (-1 if action == "up" else 1)))
                self.menu_focus(True)
            elif action == "right":
                self.zone = "content"
                self.menu_focus(False)
                self.page().focus_in()
            elif action == "ok":
                self.menu_items[self.menu_pos].activate()
            return
        if self.page().handle(action) == "menu":
            self.zone = "menu"
            self.page().focus_out()
            self.menu_focus(True)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        page = self.page()
        if isinstance(page, SearchPage) and self.zone == "content" and not self.saver.isVisible():
            if e.text() and (e.text().isalnum() or e.text() == " ") and not e.modifiers() & Qt.ControlModifier:
                self._last_input = time.monotonic()
                page.type_text(e.text())
                return
            if k == Qt.Key_Backspace and page.query:
                self._last_input = time.monotonic()
                page.type_text("\b")
                return
        keys = {Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
                Qt.Key_Return: "ok", Qt.Key_Enter: "ok", Qt.Key_Space: "ok", Qt.Key_Select: "ok",
                Qt.Key_Escape: "back", Qt.Key_Backspace: "back", Qt.Key_Back: "back",
                Qt.Key_MediaPlay: "play", Qt.Key_MediaPause: "play", Qt.Key_MediaTogglePlayPause: "play",
                Qt.Key_P: "play", Qt.Key_MediaNext: "chapter+", Qt.Key_MediaPrevious: "chapter-",
                Qt.Key_PageUp: "skip-", Qt.Key_PageDown: "skip+"}
        if k == Qt.Key_F10:
            self.leave()
        elif k in keys:
            self.act(keys[k])
        else:
            super().keyPressEvent(e)

    def _pad(self, name: str) -> None:
        mapping = {"a": "ok", "b": "back", "x": "play", "y": "search", "start": "settings", "back": "playing",
                   "lb": "chapter-", "rb": "chapter+", "lt": "skip-", "rt": "skip+"}
        if name in ("up", "down", "left", "right"):
            self.act(name)
        elif name in mapping:
            self.act(mapping[name])
        if self.isActiveWindow() is False:
            self.activateWindow()

    def _volume_step(self, step: float) -> None:
        self._last_input = time.monotonic()
        v = max(0.0, min(1.0, float(self.main.settings.get("volume", 0.9)) + step))
        self.main._volume_changed(v)
        self.main.player_bar.set_volume(v)
        if isinstance(self.page(), NowPlayingPage):
            self.page().refresh()

    def mousePressEvent(self, e) -> None:
        self._last_input = time.monotonic()
        self.saver.hide()
        super().mousePressEvent(e)

    def wheelEvent(self, e) -> None:
        self._last_input = time.monotonic()
        super().wheelEvent(e)

    # ---- live state
    def _narration_changed(self, *_):
        page = self.page()
        if isinstance(page, NowPlayingPage):
            page.refresh()
        if self.saver.isVisible():
            self.saver.update()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M"))
        s = self.main.settings
        playing = self.main.book is not None and self.main.narrator.state == "playing"
        wait = int(s.get("tv_saver_playing", 30)) if playing else int(s.get("tv_saver_idle", 300))
        if wait and not self.saver.isVisible() and time.monotonic() - self._last_input > wait:
            self.saver.start()
        if isinstance(self.page(), NowPlayingPage):
            self.page().refresh_sleep()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.saver.setGeometry(self.rect())


# ------------------------------------------------------------------------------------------ the pages
class HomePage(TvPage):
    def __init__(self, tv: TvWindow):
        super().__init__(tv)
        rows = tv.rows()
        shelves = [(t, items) for t, items in views.home_shelves(rows) if items]
        if not shelves:
            self.heading("Your library is empty", 34)
            self.note("Add books in the normal window (press F10 to leave TV mode).")
        for title, items in shelves:
            self.heading(title)
            self.add_row([tv.book_card(r) for r in items[:30]])
        self.finish()


class LibraryPage(TvPage):
    def __init__(self, tv: TvWindow):
        lib = tv.main.settings.get("current_library") or L.ALL
        super().__init__(tv, lib)
        rows = views.apply_sort(tv.rows(), "Title")
        self.note(f"{len(rows)} books")
        self.add_grid([tv.book_card(r) for r in rows])
        self.finish()


class SeriesPage(TvPage):
    def __init__(self, tv: TvWindow):
        super().__init__(tv, "Series")
        groups = views.series_groups(tv.rows())
        cards = [TvCard(name, f"{len(books)} book{'s' if len(books) != 1 else ''}", books[0].get("cover"),
                        lambda n=name, b=books: tv.show_page(BooksPage(tv, n, b, with_index=True)),
                        count=len(books), kind="series") for name, books in groups]
        self.note(f"{len(cards)} series")
        self.add_grid(cards)
        self.finish()


class AuthorsPage(TvPage):
    def __init__(self, tv: TvWindow):
        super().__init__(tv, "Authors")
        groups = views.author_groups(tv.rows())
        cards = [TvCard(name, f"{len(books)} book{'s' if len(books) != 1 else ''}", books[0].get("cover"),
                        lambda n=name, b=books: tv.show_page(BooksPage(tv, n, b)), count=len(books), kind="author")
                 for name, books in groups]
        self.note(f"{len(cards)} authors")
        self.add_grid(cards)
        self.finish()


class BooksPage(TvPage):
    def __init__(self, tv: TvWindow, title: str, books: list[dict], with_index: bool = False):
        super().__init__(tv, title)
        cards = []
        for r in books:
            c = tv.book_card(r)
            if with_index and r.get("series_index") is not None:
                c.subtitle = f"Book {views.fmt_index(r['series_index'])}"
            cards.append(c)
        self.add_grid(cards)
        self.finish()


class BookPage(TvPage):
    def __init__(self, tv: TvWindow, book_id: int):
        super().__init__(tv)
        self.book_id = book_id
        r = tv.main.library.get(book_id) or {}
        self.r = r
        top = QWidget()
        top.setStyleSheet("background: transparent;")
        h = QHBoxLayout(top)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(40)
        cover = QLabel()
        pm = QPixmap(340, 510)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        paint_cover(p, QRectF(0, 0, 340, 510), r.get("cover"), r.get("title") or "", r.get("author") or "", radius=8)
        p.end()
        cover.setPixmap(pm)
        cover.setStyleSheet("background: transparent;")
        h.addWidget(cover, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(10)
        t = QLabel(r.get("title") or "")
        _size(t, 40, True)
        t.setWordWrap(True)
        info.addWidget(t)
        sub = [r.get("author") or ""]
        if r.get("series"):
            sub.append(f"{r['series']}" + (f" · Book {views.fmt_index(r['series_index'])}"
                                           if r.get("series_index") is not None else ""))
        a = QLabel("   •   ".join(s for s in sub if s))
        _size(a, 22)
        a.setStyleSheet(f"color: {theme.MUTED};")
        info.addWidget(a)
        chars = r.get("chars") or 0
        speed = float(tv.main.settings.get("speed", 1.0))
        bits = []
        if chars:
            bits.append(f"about {L.fmt_duration(L.est_seconds(chars, speed))}")
        if r.get("finished"):
            bits.append("finished")
        elif r.get("started"):
            bits.append(f"{int((r.get('progress') or 0) * 100)}% read")
        if bits:
            m = QLabel("  ·  ".join(bits))
            _size(m, 20)
            m.setStyleSheet(f"color: {theme.ACCENT};")
            info.addWidget(m)
        desc = (r.get("description") or "").strip()
        if desc:
            d = QLabel(desc[:900] + ("…" if len(desc) > 900 else ""))
            _size(d, 19)
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {theme.TEXT};")
            d.setMaximumWidth(1100)
            info.addWidget(d)
        info.addStretch(1)
        h.addLayout(info, 1)
        self.body.addWidget(top)
        playing_this = tv.main.book_id == book_id
        label = "Continue" if (r.get("started") and not r.get("finished")) or playing_this else "Play"
        buttons = [TvButton(label, self._play, theme.I["play"], height=70, px=24)]
        if r.get("started") or playing_this:
            buttons.append(TvButton("Start over", self._start_over, theme.I["refresh"], height=70, px=22))
        buttons.append(TvButton("Chapters", lambda: tv.show_page(ChaptersPage(tv, book_id)), theme.I["list"],
                                height=70, px=22))
        self.add_buttons(buttons)
        self.finish()

    def _play(self) -> None:
        m = self.tv.main
        if m.book_id == self.book_id and m.book is not None:
            if m.narrator.state != "playing":
                m._toggle()
        else:
            m.play_book(self.book_id)
        self.tv.open_root("playing")

    def _start_over(self) -> None:
        self.tv.main.play_book(self.book_id, chapter=0)
        self.tv.open_root("playing")


class ChaptersPage(TvPage):
    def __init__(self, tv: TvWindow, book_id: int):
        r = tv.main.library.get(book_id) or {}
        super().__init__(tv, f"Chapters – {r.get('title') or ''}")
        self.book_id = book_id
        self.wait = self.note("Opening the book…")
        tv.main.parse_async(book_id, self._fill)

    def _fill(self, book) -> None:
        try:
            self.wait.hide()
        except RuntimeError:            # the page was closed meanwhile
            return
        width = min(1200, self.tv.width() - 420)
        items = [TvButton(f"{ci + 1}.  {ch.title}", lambda c=ci: self._play(c), width=width, height=56,
                          align=Qt.AlignLeft, px=19) for ci, ch in enumerate(book.chapters)]
        self.body.takeAt(self.body.count() - 1) if self.body.count() and self.body.itemAt(self.body.count() - 1).spacerItem() else None
        self.add_buttons(items, vertical=True)
        self.finish()
        cur = self.tv.main.book_id == self.book_id and self.tv.main.book is not None
        start = book.chapter_of(self.tv.main.narrator.current) if cur and book.units else 0
        self.set_pos(start, 0)

    def _play(self, ci: int) -> None:
        self.tv.main.play_book(self.book_id, chapter=ci)
        self.tv.open_root("playing")


class NowPlayingPage(TvPage):
    def __init__(self, tv: TvWindow):
        super().__init__(tv)
        m = tv.main
        if m.book is None:
            self.heading("Nothing is playing", 34)
            self.note("Pick a book on Home or in the Library and press OK.")
            self.add_buttons([TvButton("Go to Home", lambda: tv.open_root("home"), theme.I["home"], height=66)])
            self.finish()
            self.empty = True
            return
        self.empty = False
        r = m.library.get(m.book_id) or {}
        top = QWidget()
        top.setStyleSheet("background: transparent;")
        h = QHBoxLayout(top)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(44)
        cover = QLabel()
        pm = QPixmap(380, 570)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        paint_cover(p, QRectF(0, 0, 380, 570), r.get("cover"), r.get("title") or "", r.get("author") or "", radius=8)
        p.end()
        cover.setPixmap(pm)
        h.addWidget(cover, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(12)
        t = QLabel(r.get("title") or m.book.title)
        _size(t, 38, True)
        t.setWordWrap(True)
        info.addWidget(t)
        a = QLabel(r.get("author") or m.book.author or "")
        _size(a, 22)
        a.setStyleSheet(f"color: {theme.MUTED};")
        info.addWidget(a)
        self.chapter = QLabel("")
        _size(self.chapter, 22, True)
        self.chapter.setStyleSheet(f"color: {theme.ACCENT};")
        info.addWidget(self.chapter)
        self.sentence = QLabel("")
        _size(self.sentence, 30)
        self.sentence.setWordWrap(True)
        self.sentence.setMinimumHeight(200)
        self.sentence.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.sentence.setStyleSheet(f"color: {theme.TEXT}; padding: 18px 0;")
        info.addWidget(self.sentence, 1)
        self.bar = ProgressStrip()
        info.addWidget(self.bar)
        times = QHBoxLayout()
        self.elapsed = QLabel("")
        self.left = QLabel("")
        for w in (self.elapsed, self.left):
            _size(w, 18)
            w.setStyleSheet(f"color: {theme.MUTED};")
        times.addWidget(self.elapsed)
        times.addStretch(1)
        times.addWidget(self.left)
        info.addLayout(times)
        h.addLayout(info, 1)
        self.body.addWidget(top)
        self.play_btn = TvButton("Play", lambda: tv.act("play"), theme.I["play"], width=170, height=76, px=24)
        self.speed_btn = TvButton("", self._speed, width=150, height=76, px=22)
        self.sleep_btn = TvButton("", self._sleep, theme.I["moon"], width=230, height=76, px=20)
        self.add_buttons([
            TvButton("", lambda: tv.act("chapter-"), theme.I["prev"], width=86, height=76, px=24),
            TvButton("", lambda: tv.act("skip-"), theme.I["rewind"], width=86, height=76, px=24),
            self.play_btn,
            TvButton("", lambda: tv.act("skip+"), theme.I["forward"], width=86, height=76, px=24),
            TvButton("", lambda: tv.act("chapter+"), theme.I["next"], width=86, height=76, px=24),
            self.speed_btn, self.sleep_btn])
        self.hint = self.note("LB / RB: chapter   ·   LT / RT: a few sentences back / on   ·   "
                              "right stick: volume", 16)
        self.finish()
        self.pos = (0, 2)
        self.refresh()

    def set_pos(self, li: int, ci: int) -> None:
        super().set_pos(li, ci)

    def refresh(self) -> None:
        if getattr(self, "empty", True):
            return
        m = self.tv.main
        b = m.book
        if b is None or not b.unit_count():
            return
        u = m.narrator.current
        ci = b.chapter_of(u) if b.units else 0
        self.chapter.setText(f"Chapter {ci + 1} of {len(b.chapters)}  ·  {b.chapters[ci].title}"
                             if b.chapters else "")
        self.sentence.setText(b.unit_text(u))
        units = b.chapter_units(ci)
        cum = m._cum or [0]
        c0, c1 = cum[units.start], cum[min(units.stop, len(cum) - 1)]
        done = cum[min(u, len(cum) - 1)]
        speed = float(m.settings.get("speed", 1.0))
        self.bar.set_value((done - c0) / max(1, c1 - c0), done / max(1, cum[-1]))
        self.elapsed.setText(f"{L.fmt_clock(L.est_seconds(done - c0, speed))} in this chapter")
        self.left.setText(f"{L.fmt_clock(L.est_seconds(cum[-1] - done, speed))} left in the book  ·  "
                          f"volume {int(float(m.settings.get('volume', 0.9)) * 100)}%")
        playing = m.narrator.state in ("playing", "loading")
        self.play_btn.glyph = theme.I["pause" if playing else "play"]
        self.play_btn.set_text("Pause" if playing else "Play")
        self.speed_btn.set_text(f"{speed:g}×")
        self.refresh_sleep()

    def refresh_sleep(self) -> None:
        if getattr(self, "empty", True):
            return
        m = self.tv.main
        if m._sleep_chapter is not None:
            text = "End of chapter"
        elif m._sleep_end is not None:
            left = max(0, int(m._sleep_end - time.time()))
            text = f"{left // 60}:{left % 60:02d}"
        else:
            text = "Sleep off"
        self.sleep_btn.set_text(text)

    def _speed(self) -> None:
        m = self.tv.main
        cur = float(m.settings.get("speed", 1.0))
        nxt = next((s for s in SPEEDS if s > cur + 1e-6), SPEEDS[0])
        m._speed_changed(nxt)
        m.player_bar.set_speed(nxt)
        self.refresh()

    def _sleep(self) -> None:
        m = self.tv.main
        values = [v for _, v in SLEEP]
        cur = "chapter" if m._sleep_chapter is not None else (
            min((v for v in values if isinstance(v, int)), key=lambda v: abs(v * 60 - (m._sleep_end - time.time())))
            if m._sleep_end is not None else None)
        i = values.index(cur) if cur in values else 0
        m._sleep_chosen(values[(i + 1) % len(values)])
        self.refresh_sleep()


class ProgressStrip(QWidget):
    """Chapter progress (thick) over book progress (thin)."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(22)
        self.chapter, self.book = 0.0, 0.0

    def set_value(self, chapter: float, book: float) -> None:
        self.chapter, self.book = max(0.0, min(1.0, chapter)), max(0.0, min(1.0, book))
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(0, 0, w, 10), 5, 5)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(0, 0, w * self.chapter, 10), 5, 5)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(0, 15, w, 4), 2, 2)
        p.setBrush(QColor(theme.MUTED))
        p.drawRoundedRect(QRectF(0, 15, w * self.book, 4), 2, 2)


class SearchPage(TvPage):
    KEYS = ["abcdefghi", "jklmnopqr", "stuvwxyz0", "123456789"]

    def __init__(self, tv: TvWindow):
        super().__init__(tv, "Search")
        self.query = ""
        self.field = QLabel("")
        _size(self.field, 30, True)
        self.field.setStyleSheet(f"color: {theme.TEXT}; background: {theme.SURFACE2}; border-radius: 12px; "
                                 f"padding: 14px 22px; border: 1px solid {theme.BORDER};")
        self.field.setMaximumWidth(900)
        self.body.addWidget(self.field)
        for row in self.KEYS:
            self.add_buttons([TvButton(ch.upper(), lambda c=ch: self.type_text(c), width=66, height=60, px=22)
                              for ch in row])
        self.add_buttons([TvButton("Space", lambda: self.type_text(" "), width=200, height=60),
                          TvButton("Delete", lambda: self.type_text("\b"), theme.I["backspace"], width=170, height=60),
                          TvButton("Clear", lambda: self.type_text("\x00"), width=130, height=60)])
        self.n_keys = len(self.lines)
        self.results_head = self.heading("")
        self.results_box = QVBoxLayout()
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setLayout(self.results_box)
        self.body.addWidget(holder)
        self.finish()
        self._show()

    def type_text(self, ch: str) -> None:
        if ch == "\b":
            self.query = self.query[:-1]
        elif ch == "\x00":
            self.query = ""
        else:
            self.query += ch
        self._show()

    def _show(self) -> None:
        self.field.setText((self.query or "Type with the keys below or a keyboard") + ("│" if self.query else ""))
        self.field.setStyleSheet(self.field.styleSheet().replace(f"color: {theme.MUTED}", f"color: {theme.TEXT}")
                                 if self.query else self.field.styleSheet())
        # drop old results (lines after the keyboard)
        for line in self.lines[self.n_keys:]:
            for it in line:
                it.hide()
                it.deleteLater()
        self.lines = self.lines[:self.n_keys]
        while self.results_box.count():
            w = self.results_box.takeAt(0).widget()
            if w:
                w.hide()                  # gone at once, not when Qt gets round to deleting it
                w.deleteLater()
        if len(self.query.strip()) < 2:
            self.results_head.setText("")
            return
        rows = views.apply_sort(views.search(self.tv.rows(), self.query), "Title")[:40]
        self.results_head.setText(f"{len(rows)} result{'s' if len(rows) != 1 else ''}" if rows else "Nothing found")
        cards = [self.tv.book_card(r) for r in rows]
        if not cards:
            return
        cols = self.tv.grid_cols(CARD_W)
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(18)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for n, c in enumerate(cards):
            grid.addWidget(c, n // cols, n % cols)
        self.results_box.addWidget(box)
        for k in range(0, len(cards), cols):
            self.lines.append(cards[k:k + cols])
        self._wire(cards)


class SettingsPage(TvPage):
    def __init__(self, tv: TvWindow):
        super().__init__(tv, "TV mode settings")
        s = tv.main.settings
        self.start_btn = TvButton("", self._toggle_start, width=620, height=62, align=Qt.AlignLeft)
        self.saver_play = TvButton("", lambda: self._cycle("tv_saver_playing", SAVER_PLAYING), width=620, height=62,
                                   align=Qt.AlignLeft)
        self.saver_idle = TvButton("", lambda: self._cycle("tv_saver_idle", SAVER_IDLE), width=620, height=62,
                                   align=Qt.AlignLeft)
        self.pad_note = None
        self.add_buttons([self.start_btn, self.saver_play, self.saver_idle,
                          TvButton("Leave TV mode", tv.leave, theme.I["exit"], width=620, height=62, align=Qt.AlignLeft),
                          TvButton("Close Ai Book Reader", self._quit, theme.I["close"], width=620, height=62,
                                   align=Qt.AlignLeft)], vertical=True)
        now = f"{tv.pad.connected} connected. " if tv.pad.connected else "No controller connected right now. "
        self.note("Controller: " + now + "Xbox controllers and PlayStation DualShock 4 / DualSense (USB or "
                  "Bluetooth) work: cross / A = OK, circle / B = back, square / X = play or pause.", 17)
        self.finish()
        self._labels()
        s.save()

    def _labels(self) -> None:
        s = self.tv.main.settings
        self.start_btn.set_text(f"Start in TV mode:  {'On' if s.get('tv_start') else 'Off'}")
        sp = dict(SAVER_PLAYING).get(int(s.get("tv_saver_playing", 30)), "30 seconds")
        si = dict(SAVER_IDLE).get(int(s.get("tv_saver_idle", 300)), "5 minutes")
        self.saver_play.set_text(f"Screen saver while a book plays:  {sp}")
        self.saver_idle.set_text(f"Screen saver when nothing plays:  {si}")

    def _toggle_start(self) -> None:
        s = self.tv.main.settings
        s["tv_start"] = not bool(s.get("tv_start"))
        s.save()
        self._labels()

    def _cycle(self, key: str, options: list[tuple[int, str]]) -> None:
        s = self.tv.main.settings
        values = [v for v, _ in options]
        cur = int(s.get(key, values[0]))
        s[key] = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
        s.save()
        self._labels()

    def _quit(self) -> None:
        main = self.tv.main
        self.tv.leave()
        QTimer.singleShot(50, main.close)


# ------------------------------------------------------------------------------------------ screen saver
class TvSaver(QWidget):
    """Dark screen with the cover and the sentence being read, moving slowly so nothing burns in."""

    def __init__(self, tv: TvWindow):
        super().__init__(tv)
        self.tv = tv
        self.spot = QPoint(100, 100)
        self.timer = QTimer(self)
        self.timer.setInterval(9000)
        self.timer.timeout.connect(self._move)
        self.hide()

    def start(self) -> None:
        self.setGeometry(self.tv.rect())
        self._move()
        self.show()
        self.raise_()
        self.timer.start()

    def hideEvent(self, e) -> None:
        self.timer.stop()
        super().hideEvent(e)

    def _move(self) -> None:
        w, h = max(1, self.width() - 760), max(1, self.height() - 420)
        self.spot = QPoint(random.randint(40, max(41, w)), random.randint(40, max(41, h)))
        self.update()

    def mousePressEvent(self, e) -> None:
        self.tv.act("wake")

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        m = self.tv.main
        x, y = self.spot.x(), self.spot.y()
        p.setOpacity(0.55)
        if m.book is not None:
            r = m.library.get(m.book_id) or {}
            paint_cover(p, QRectF(x, y, 220, 330), r.get("cover"), r.get("title") or "", radius=6)
            p.setPen(QColor(235, 235, 235))
            p.setFont(_font(28, True))
            p.drawText(QRectF(x + 250, y, 500, 80), Qt.AlignLeft | Qt.TextWordWrap, r.get("title") or "")
            p.setFont(_font(22))
            p.setPen(QColor(190, 190, 190))
            text = m.book.unit_text(m.narrator.current) if m.book.unit_count() else ""
            p.drawText(QRectF(x + 250, y + 96, 500, 240), Qt.AlignLeft | Qt.TextWordWrap, text)
        else:
            p.setPen(QColor(200, 200, 200))
            p.setFont(_font(64, True))
            p.drawText(QRectF(x, y, 700, 120), Qt.AlignLeft, time.strftime("%H:%M"))
            p.setFont(_font(24))
            p.drawText(QRectF(x, y + 110, 700, 60), Qt.AlignLeft, "Ai Book Reader")
