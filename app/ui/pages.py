"""ABS-style pages: shelf pages (Library / Series / Authors / one series / one author), Home, book detail."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from ..library.libraries import est_seconds, fmt_duration
from ..library.views import FILTERS, SORTS, fmt_index
from . import theme
from .shelf import Shelf, ShelfItem, paint_cover
from .widgets import Bar, IconButton, muted


def book_item(r: dict, with_index: bool = False) -> ShelfItem:
    badge = f"#{fmt_index(r.get('series_index'))}" if with_index and r.get("series_index") is not None else ""
    return ShelfItem("book", r["id"], r["title"] or "", r.get("author") or "", [r.get("cover")],
                     progress=r.get("progress") or 0.0, finished=bool(r.get("finished")), badge=badge, data=r)


# ------------------------------------------------------------------------------------------ zoom
class ZoomPill(QFrame):
    changed = Signal(int)
    STEPS = [80, 100, 120, 140, 160, 180, 200, 230]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ZoomPill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(4)
        self.minus = IconButton("minus", "Smaller covers", 12)
        self.plus = IconButton("add", "Bigger covers", 12)
        self.label = QLabel("120")
        self.label.setStyleSheet("font-size: 11pt; padding: 0 4px;")
        for w in (self.minus, self.label, self.plus):
            lay.addWidget(w)
        self.value = 120
        self.minus.clicked.connect(lambda: self._step(-1))
        self.plus.clicked.connect(lambda: self._step(1))

    def set_value(self, v: int) -> None:
        self.value = min(self.STEPS, key=lambda s: abs(s - v))
        self.label.setText(str(self.value))

    def _step(self, d: int) -> None:
        i = self.STEPS.index(self.value) if self.value in self.STEPS else 2
        i = max(0, min(len(self.STEPS) - 1, i + d))
        self.set_value(self.STEPS[i])
        self.changed.emit(self.value)


# ------------------------------------------------------------------------------------------ shelf page
class ShelfPage(QWidget):
    """Toolbar (count or back+title, filter, sort) over a wooden shelf, with the cover-size pill."""
    itemActivated = Signal(object)
    itemPlay = Signal(object)
    itemMenu = Signal(object, QPoint)
    back = Signal()
    viewChanged = Signal()          # filter / sort / direction changed
    zoomChanged = Signal(int)

    def __init__(self, filters: bool = True, sorts: list[str] | None = SORTS, back: bool = False, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar = Bar("ToolBar", margins=(18, 7, 14, 7))
        if back:
            b = self.bar.add(IconButton("back", "Back (Alt+Left)", 14))
            b.clicked.connect(self.back)
        self.title = QLabel("")
        self.title.setObjectName("PageTitle")
        self.bar.add(self.title)
        self.bar.stretch()
        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.filter.setMinimumWidth(170)
        self.filter.setVisible(filters)
        self.sort = QComboBox()
        self.sort.setMinimumWidth(150)
        if sorts:
            self.sort.addItems(sorts)
        self.sort.setVisible(bool(sorts))
        self.desc = IconButton("up", "Sort direction", 12)
        self.desc.setCheckable(True)
        self.desc.setVisible(bool(sorts))
        self.desc.toggled.connect(lambda on: self.desc.setText(theme.I["down"] if on else theme.I["up"]))
        for w in (self.filter, self.sort, self.desc):
            self.bar.add(w)
        lay.addWidget(self.bar)
        self.shelf = Shelf()
        lay.addWidget(self.shelf, 1)
        self.zoom = ZoomPill(self)
        self.zoom.changed.connect(self.zoomChanged)
        self.shelf.activated.connect(self.itemActivated)
        self.shelf.playRequested.connect(self.itemPlay)
        self.shelf.menuRequested.connect(self.itemMenu)
        self.filter.currentIndexChanged.connect(lambda _: self.viewChanged.emit())
        self.sort.currentIndexChanged.connect(lambda _: self.viewChanged.emit())
        self.desc.toggled.connect(lambda _: self.viewChanged.emit())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.zoom.adjustSize()
        self.zoom.move(self.width() - self.zoom.width() - 26, self.height() - self.zoom.height() - 18)
        self.zoom.raise_()

    def set_items(self, items: list[ShelfItem], title: str, keep_scroll: bool = False) -> None:
        self.title.setText(title)
        self.shelf.set_items(items, keep_scroll)


# ------------------------------------------------------------------------------------------ home
class HomePage(QScrollArea):
    itemActivated = Signal(object)
    itemPlay = Signal(object)
    itemMenu = Signal(object, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setStyleSheet(f"background: #3a2818;")
        self.vbox = QVBoxLayout(self.inner)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.setSpacing(0)
        self.setWidget(self.inner)
        self.shelves: list[Shelf] = []
        self.cover_w = 120
        self.title_mode = "under"         # titles: off / cover / under (label on the shelf)

    def set_shelves(self, shelves: list[tuple[str, list[ShelfItem]]], empty_text: str = "") -> None:
        while self.vbox.count():
            w = self.vbox.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.shelves = []
        if not shelves:
            s = Shelf()
            s.empty_text = empty_text or "Add a folder with books to get started"
            s.set_items([])
            self.vbox.addWidget(s, 1)
            return
        for title, items in shelves:
            s = Shelf(single_row=True)
            s.title_mode = self.title_mode
            s.set_cover_width(self.cover_w)
            s.set_items(items)
            s.activated.connect(self.itemActivated)
            s.playRequested.connect(self.itemPlay)
            s.menuRequested.connect(self.itemMenu)
            label = QLabel(title, s.viewport())
            label.setObjectName("ShelfLabel")
            label.adjustSize()
            label.move(26, max(2, s.top_pad // 2 - label.height() // 2))
            self.vbox.addWidget(s)
            self.shelves.append(s)
        filler = Shelf()             # empty shelves below, like an ABS bookcase
        filler.empty_text = ""
        filler.title_mode = self.title_mode
        filler.set_cover_width(self.cover_w)
        filler.setMinimumHeight(filler.row_h)
        filler.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.vbox.addWidget(filler, 1)

    def set_cover_width(self, w: int) -> None:
        self.cover_w = w
        for s in self.shelves:
            s.set_cover_width(w)


# ------------------------------------------------------------------------------------------ book page
class CoverLabel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path, self.title, self.author = None, "", ""
        self.setFixedSize(260, 390)

    def set_cover(self, path, title, author) -> None:
        self.path, self.title, self.author = path, title, author
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(4, 4, self.width() - 12, self.height() - 12)
        for k, a in ((8, 35), (5, 55), (2, 80)):
            p.fillRect(r.adjusted(k * 0.6, k, k, k * 0.4), QColor(0, 0, 0, a))
        paint_cover(p, r, self.path, self.title, self.author, radius=4)


class BookPage(QScrollArea):
    play = Signal(int, object)        # book id, chapter index (or None = continue)
    read = Signal(int)
    export = Signal(int)
    seriesClicked = Signal(str)
    authorClicked = Signal(str)
    moreClicked = Signal(int, object)  # book id, global pos
    charactersClicked = Signal(int, object)
    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.book_id: int | None = None
        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(30, 18, 30, 30)
        top = QHBoxLayout()
        back = IconButton("back", "Back (Alt+Left)", 15)
        back.clicked.connect(self.back)
        top.addWidget(back)
        top.addStretch(1)
        outer.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(36)
        left = QVBoxLayout()
        self.cover = CoverLabel()
        left.addWidget(self.cover)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(248)
        self.progress.setFixedHeight(6)
        left.addWidget(self.progress, 0, Qt.AlignHCenter)
        self.progress_text = muted("")
        self.progress_text.setAlignment(Qt.AlignCenter)
        left.addWidget(self.progress_text)
        left.addStretch(1)
        row.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 22pt; font-weight: 600;")
        right.addWidget(self.title)
        self.series_btn = QPushButton("")
        self.series_btn.setObjectName("Link")
        self.series_btn.setStyleSheet("font-size: 12pt; color: #d0d0d0;")
        self.series_btn.setCursor(Qt.PointingHandCursor)
        self.series_btn.clicked.connect(lambda: self.seriesClicked.emit(self._series or ""))
        right.addWidget(self.series_btn, 0, Qt.AlignLeft)
        self.authors_row = QHBoxLayout()
        self.authors_row.setSpacing(4)
        right.addLayout(self.authors_row)
        right.addSpacing(10)
        self.info = QGridLayout()
        self.info.setHorizontalSpacing(18)
        self.info.setVerticalSpacing(5)
        self.info.setColumnStretch(0, 0)
        self.info.setColumnStretch(1, 1)
        right.addLayout(self.info)
        right.addSpacing(14)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setObjectName("Play")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(lambda: self.play.emit(self.book_id, None))
        self.read_btn = QPushButton("Read")
        self.read_btn.setCursor(Qt.PointingHandCursor)
        self.read_btn.clicked.connect(lambda: self.read.emit(self.book_id))
        self.export_btn = IconButton("save", "Make an M4B audiobook", 16)
        self.export_btn.clicked.connect(lambda: self.export.emit(self.book_id))
        self.chars_btn = IconButton("people", "Character voices", 16)
        self.chars_btn.clicked.connect(lambda: self.charactersClicked.emit(
            self.book_id, self.chars_btn.mapToGlobal(self.chars_btn.rect().bottomLeft())))
        self.more_btn = IconButton("more", "More", 16)
        self.more_btn.clicked.connect(lambda: self.moreClicked.emit(
            self.book_id, self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft())))
        for w in (self.play_btn, self.read_btn, self.export_btn, self.chars_btn, self.more_btn):
            buttons.addWidget(w)
        buttons.addStretch(1)
        right.addLayout(buttons)
        right.addSpacing(16)
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setTextFormat(Qt.PlainText)
        self.desc.setStyleSheet("font-size: 10.5pt; color: #dcdcdc; line-height: 140%;")
        self.desc.setMaximumWidth(900)
        right.addWidget(self.desc)
        self.more_desc = QPushButton("Read more")
        self.more_desc.setObjectName("Link")
        self.more_desc.setStyleSheet(f"color: {theme.MUTED};")
        self.more_desc.clicked.connect(self._toggle_desc)
        right.addWidget(self.more_desc, 0, Qt.AlignLeft)
        right.addSpacing(18)
        self.chapters_title = QLabel("Chapters")
        self.chapters_title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        right.addWidget(self.chapters_title)
        self.chapters = QTableWidget(0, 3)
        self.chapters.setHorizontalHeaderLabels(["#", "Title", "Length"])
        self.chapters.verticalHeader().setVisible(False)
        self.chapters.setEditTriggers(QTableWidget.NoEditTriggers)
        self.chapters.setSelectionBehavior(QTableWidget.SelectRows)
        self.chapters.setShowGrid(False)
        self.chapters.setAlternatingRowColors(True)
        self.chapters.setStyleSheet("QTableWidget { background: #2a2a2a; alternate-background-color: #303030; "
                                    "border-radius: 6px; } QTableWidget::item { padding: 4px 8px; }")
        hh = self.chapters.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.chapters.cellDoubleClicked.connect(lambda r, c: self.play.emit(self.book_id, r))
        self.chapters.setToolTip("Double-click a chapter to read from there")
        self.chapters.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right.addWidget(self.chapters)
        right.addStretch(1)
        row.addLayout(right, 1)
        outer.addLayout(row)
        self.setWidget(inner)
        self._series = None
        self._full_desc = ""
        self._desc_open = False
        self.restyle()

    def restyle(self) -> None:
        self.chapters.setStyleSheet(
            f"QTableWidget {{ background: {theme.SURFACE}; alternate-background-color: {theme.SURFACE2}; "
            "border-radius: 6px; } QTableWidget::item { padding: 4px 8px; }")
        self.desc.setStyleSheet(f"font-size: 10.5pt; color: {theme.READER_TEXT};")
        self.series_btn.setStyleSheet(f"font-size: 12pt; color: {theme.MUTED};")
        self.more_desc.setStyleSheet(f"color: {theme.MUTED};")
        self.cover.update()

    def _clear_layout(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def show_book(self, r: dict, library: str, narrator: str, characters: str, speed: float) -> None:
        self.book_id = r["id"]
        self.cover.set_cover(r.get("cover"), r["title"], r.get("author") or "")
        self.title.setText(r["title"])
        self._series = r.get("series")
        if self._series:
            idx = fmt_index(r.get("series_index"))
            self.series_btn.setText(f"{self._series}" + (f"  #{idx}" if idx else ""))
            self.series_btn.show()
        else:
            self.series_btn.hide()
        self._clear_layout(self.authors_row)
        by = QLabel("by")
        by.setObjectName("Muted")
        self.authors_row.addWidget(by)
        from ..library.views import authors_of
        names = authors_of(r)
        for k, a in enumerate(names):
            b = QPushButton(a + ("," if k < len(names) - 1 else ""))
            b.setObjectName("Link")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("font-size: 11pt;")
            b.clicked.connect(lambda _=False, name=a: self.authorClicked.emit(name))
            self.authors_row.addWidget(b)
        self.authors_row.addStretch(1)
        self._clear_layout(self.info)
        chars = r.get("chars") or 0
        rows = [("Publisher", r.get("publisher") or ""), ("Published", str(r.get("year") or "")),
                ("Format", (r.get("fmt") or "").upper()), ("Library", library),
                ("Length", f"~{fmt_duration(est_seconds(chars, speed))} at {speed:g}×" if chars else "Open it once to measure"),
                ("Narrator", narrator), ("Character voices", characters)]
        for i, (k, v) in enumerate([x for x in rows if x[1]]):
            kl = QLabel(k)
            kl.setStyleSheet(f"color: {theme.FAINT};")
            vl = QLabel(v)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.info.addWidget(kl, i, 0, Qt.AlignLeft | Qt.AlignTop)
            self.info.addWidget(vl, i, 1, Qt.AlignLeft | Qt.AlignTop)
        prog = 1.0 if r.get("finished") else (r.get("progress") or 0.0)
        self.progress.setValue(int(prog * 1000))
        self.progress.setStyleSheet(f"QProgressBar::chunk {{ background: {theme.GREEN_HOVER if r.get('finished') else theme.ORANGE}; }}")
        if r.get("finished"):
            self.progress_text.setText("Finished")
            self.play_btn.setText("▶  Play again")
        elif r.get("started"):
            left = est_seconds(chars * (1 - prog), speed) if chars else 0
            self.progress_text.setText(f"{int(prog * 100)}% read" + (f" · {fmt_duration(left)} left" if left else ""))
            self.play_btn.setText("▶  Continue")
        else:
            self.progress_text.setText("Not started")
            self.play_btn.setText("▶  Play")
        self.progress.setVisible(prog > 0)
        self._full_desc = r.get("description") or ""
        self._desc_open = False
        self._render_desc()
        self.set_chapters(None)
        self.verticalScrollBar().setValue(0)

    def _render_desc(self) -> None:
        d = self._full_desc
        long = len(d) > 600
        self.desc.setText(d if self._desc_open or not long else d[:600].rsplit(" ", 1)[0] + " …")
        self.desc.setVisible(bool(d))
        self.more_desc.setVisible(long)
        self.more_desc.setText("Read less" if self._desc_open else "Read more")

    def _toggle_desc(self) -> None:
        self._desc_open = not self._desc_open
        self._render_desc()

    def set_chapters(self, chapters: list[tuple[str, int]] | None, speed: float = 1.0, current: int = -1) -> None:
        """chapters: (title, characters) per chapter; None = still loading."""
        t = self.chapters
        if chapters is None:
            self.chapters_title.setText("Chapters  ·  loading…")
            t.setRowCount(0)
            t.setFixedHeight(40)
            return
        self.chapters_title.setText(f"Chapters  ·  {len(chapters)}")
        t.setRowCount(len(chapters))
        for i, (title, chars) in enumerate(chapters):
            n = QTableWidgetItem(str(i + 1))
            n.setForeground(QColor(theme.FAINT))
            title_item = QTableWidgetItem(title)
            if i == current:
                title_item.setForeground(QColor(theme.YELLOW))
            t.setItem(i, 0, n)
            t.setItem(i, 1, title_item)
            t.setItem(i, 2, QTableWidgetItem(fmt_duration(est_seconds(chars, speed), short=True)))
            t.setRowHeight(i, 32)
        t.setFixedHeight(min(32 * len(chapters) + 34, 32 * 16 + 34))
