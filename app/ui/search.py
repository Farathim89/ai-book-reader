"""ABS-style search dropdown under the app bar's search box: Books, Series, Authors."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QVBoxLayout

from ..library import views
from . import theme
from .shelf import COVERS, paint_placeholder

KIND, KEY = Qt.UserRole + 1, Qt.UserRole + 2


def rank_books(rows: list[dict], text: str, limit: int = 6) -> list[dict]:
    """Search hits, best first: title starts with the text, then title contains it, then the rest."""
    t = text.lower().strip()
    hits = views.search(rows, t)

    def score(r):
        title = (r.get("title") or "").lower()
        rank = 0 if title.startswith(t) else 1 if t in title else 2
        if rank == 2:   # found through series / author: keep series volumes in order
            return (rank, (r.get("series") or "~").lower(), r.get("series_index") or 0, views.sort_title(r.get("title")))
        return (rank, "", 0, views.sort_title(r.get("title")))
    return sorted(hits, key=score)[:limit]


def match_names(names: list[tuple[str, int]], text: str, limit: int = 4) -> list[tuple[str, int]]:
    words = text.lower().split()
    hits = [(n, c) for n, c in names if all(w in n.lower() for w in words)]
    hits.sort(key=lambda nc: (not nc[0].lower().startswith(text.lower().strip()), nc[0].lower()))
    return hits[:limit]


class SearchPopup(QFrame):
    picked = Signal(str, object)        # kind ("book" / "series" / "author" / "all"), key

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("SearchPopup")
        self.setStyleSheet(f"#SearchPopup {{ background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; "
                           "border-radius: 6px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.list = QListWidget()
        self.list.setIconSize(QSize(30, 45))
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; }}"
            f"QListWidget::item {{ padding: 4px 8px; border-radius: 4px; }}"
            f"QListWidget::item:selected, QListWidget::item:hover {{ background: {theme.SURFACE3}; }}")
        self.list.itemClicked.connect(self._activate)
        lay.addWidget(self.list)
        self.hide()

    # ---- content ------------------------------------------------------------------
    def _header(self, text: str) -> None:
        it = QListWidgetItem(text.upper())
        it.setFlags(Qt.NoItemFlags)
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        it.setFont(f)
        it.setForeground(QColor(theme.FAINT))
        self.list.addItem(it)

    def _cover_icon(self, path, title: str) -> QIcon:
        pm = COVERS.get(path, 30, 45)
        if pm is None:
            pm = QPixmap(60, 90)
            pm.setDevicePixelRatio(2.0)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            from PySide6.QtCore import QRectF
            paint_placeholder(p, QRectF(0, 0, 30, 45), title[:1])
            p.end()
        return QIcon(pm)

    def show_results(self, books: list[dict], series: list[tuple[str, int]], authors: list[tuple[str, int]],
                     text: str) -> None:
        self.list.clear()
        if books:
            self._header("Books")
            for r in books:
                sub = r.get("author") or ""
                if r.get("series"):
                    sub += f"  ·  {r['series']} #{views.fmt_index(r.get('series_index'))}"
                it = QListWidgetItem(self._cover_icon(r.get("cover"), r["title"]), f"{r['title']}\n{sub}")
                it.setData(KIND, "book")
                it.setData(KEY, r["id"])
                self.list.addItem(it)
        if series:
            self._header("Series")
            for name, n in series:
                it = QListWidgetItem(theme_glyph("series"), f"{name}\n{n} book{'s' if n != 1 else ''}")
                it.setData(KIND, "series")
                it.setData(KEY, name)
                self.list.addItem(it)
        if authors:
            self._header("Authors")
            for name, n in authors:
                it = QListWidgetItem(theme_glyph("author"), f"{name}\n{n} book{'s' if n != 1 else ''}")
                it.setData(KIND, "author")
                it.setData(KEY, name)
                self.list.addItem(it)
        if not (books or series or authors):
            it = QListWidgetItem(f"No matches for “{text}”")
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(theme.MUTED))
            self.list.addItem(it)
        else:
            it = QListWidgetItem(f"Show all results for “{text}”  →")
            it.setData(KIND, "all")
            it.setData(KEY, text)
            it.setForeground(QColor(theme.YELLOW))
            self.list.addItem(it)
        rows_h = sum(self.list.sizeHintForRow(i) for i in range(self.list.count()))
        self.setFixedHeight(min(560, rows_h + 14))
        self.list.setCurrentRow(-1)
        self.show()
        self.raise_()

    # ---- keyboard (forwarded from the search box) ----------------------------------------
    def move_selection(self, d: int) -> None:
        n = self.list.count()
        i = self.list.currentRow()
        for _ in range(n):
            i = (i + d) % n if i >= 0 else (0 if d > 0 else n - 1)
            if self.list.item(i).flags() & Qt.ItemIsEnabled:
                self.list.setCurrentRow(i)
                return

    def activate_current(self) -> bool:
        it = self.list.currentItem()
        if it is None or not (it.flags() & Qt.ItemIsEnabled):
            return False
        self._activate(it)
        return True

    def _activate(self, it: QListWidgetItem) -> None:
        kind = it.data(KIND)
        if kind:
            self.hide()
            self.picked.emit(kind, it.data(KEY))


def theme_glyph(name: str) -> QIcon:
    from .widgets import glyph_icon
    return glyph_icon(theme.I[name], 22, theme.MUTED)
