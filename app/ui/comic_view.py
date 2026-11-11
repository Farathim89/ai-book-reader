"""Comic pages with the current speech bubble outlined and a subtitle line."""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QLabel

from ..formats.model import Book
from . import theme


class ComicReader(QGraphicsView):
    unitClicked = Signal(int)
    unitActivated = Signal(int)
    pageChanged = Signal(int, int)    # page, page count

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(theme.READER_BG))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.NoDrag)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene_.addItem(self.pix)
        self.outline = QGraphicsPathItem()
        self.outline.setPen(QPen(QColor(theme.ACCENT), 4))
        self.outline.setBrush(QBrush(QColor(91, 140, 255, 40)))
        self.outline.setZValue(2)
        self.scene_.addItem(self.outline)
        self.subtitle = QLabel(self)
        self.subtitle.setWordWrap(True)
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setStyleSheet(
            "background: rgba(10,11,14,215); color: #f2f4f7; border-radius: 10px; padding: 10px 16px;"
            "font-size: 12.5pt;")
        self.subtitle.hide()
        self.book: Book | None = None
        self.loader: Callable[[int], bytes] | None = None
        self.page = -1
        self._cache: OrderedDict[int, QPixmap] = OrderedDict()
        self.page_units: dict[int, list[int]] = {}

    def set_book(self, book: Book, loader: Callable[[int], bytes]) -> None:
        self.book, self.loader = book, loader
        self._cache.clear()
        self.page = -1
        self.page_units = {}
        for u in range(book.unit_count()):
            p = book.unit_para(u).page
            if p is not None:
                self.page_units.setdefault(p, []).append(u)
        self.outline.setPath(QPainterPath())
        self.subtitle.hide()

    def page_count(self) -> int:
        return len(self.book.pages) if self.book else 0

    def _pixmap(self, i: int) -> QPixmap:
        if i in self._cache:
            self._cache.move_to_end(i)
            return self._cache[i]
        pm = QPixmap()
        try:
            pm.loadFromData(self.loader(i))
        except Exception:
            pass
        self._cache[i] = pm
        while len(self._cache) > 6:
            self._cache.popitem(last=False)
        return pm

    def show_page(self, i: int) -> None:
        if not self.book or not (0 <= i < self.page_count()):
            return
        if i != self.page:
            self.page = i
            pm = self._pixmap(i)
            self.pix.setPixmap(pm)
            self.scene_.setSceneRect(QRectF(pm.rect()))
            self.outline.setPath(QPainterPath())
            self._fit()
            self.pageChanged.emit(i, self.page_count())

    def first_unit_on_or_after(self, page: int) -> int | None:
        for p in range(page, self.page_count()):
            if self.page_units.get(p):
                return self.page_units[p][0]
        return None

    def highlight(self, u: int, center: bool = False) -> None:
        if not self.book or not (0 <= u < self.book.unit_count()):
            return
        para = self.book.unit_para(u)
        if para.page is not None:
            self.show_page(para.page)
        path = QPainterPath()
        if para.box:
            r = self.pix.pixmap().rect()
            x0, y0, x1, y1 = para.box
            path.addRoundedRect(QRectF(x0 * r.width(), y0 * r.height(), (x1 - x0) * r.width(),
                                       (y1 - y0) * r.height()), 14, 14)
        self.outline.setPath(path)
        self.subtitle.setText(self.book.unit_text(u))
        self.subtitle.show()
        self._place_subtitle()

    def _fit(self) -> None:
        if not self.pix.pixmap().isNull():
            self.fitInView(self.pix, Qt.KeepAspectRatio)

    def _place_subtitle(self) -> None:
        w = min(self.viewport().width() - 40, 760)
        self.subtitle.setFixedWidth(max(200, w))
        self.subtitle.adjustSize()
        self.subtitle.move((self.width() - self.subtitle.width()) // 2, self.height() - self.subtitle.height() - 16)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit()
        if self.subtitle.isVisible():
            self._place_subtitle()

    def wheelEvent(self, e) -> None:
        if e.angleDelta().y() < 0:
            self.show_page(self.page + 1)
        elif e.angleDelta().y() > 0:
            self.show_page(self.page - 1)

    def _unit_at(self, pos) -> int | None:
        if not self.book:
            return None
        pt = self.mapToScene(pos)
        r = self.pix.pixmap().rect()
        if r.width() == 0:
            return None
        nx, ny = pt.x() / r.width(), pt.y() / r.height()
        for u in self.page_units.get(self.page, []):
            box = self.book.unit_para(u).box
            if box and box[0] <= nx <= box[2] and box[1] <= ny <= box[3]:
                return u
        return None

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton:
            u = self._unit_at(e.position().toPoint())
            if u is not None:
                self.unitClicked.emit(u)

    def mouseDoubleClickEvent(self, e) -> None:
        u = self._unit_at(e.position().toPoint())
        if u is not None:
            self.unitActivated.emit(u)
