"""Book text with the spoken sentence highlighted; click a sentence to read from there."""
from __future__ import annotations

import bisect

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QFrame, QMenu, QTextBrowser, QTextEdit

from ..formats.model import Book
from . import theme

MAX_COLUMN = 760


class TextReader(QTextBrowser):
    unitClicked = Signal(int)
    unitActivated = Signal(int)   # "read from here"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._side = -1
        self.setOpenLinks(False)
        self.setReadOnly(True)
        # mouse selection only: the arrow keys belong to the sentence-skip shortcuts
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.viewport().setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QTextBrowser {{ background: {theme.READER_BG}; color: {theme.READER_TEXT}; border: none; }}")
        self.book: Book | None = None
        self.ci = -1
        self.starts: list[int] = []
        self.units: list[int] = []
        self.ranges: dict[int, tuple[int, int]] = {}
        self.current: int | None = None
        self.family = "Georgia"
        self.size = 16
        self._press: QPoint | None = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def restyle(self) -> None:
        self.setStyleSheet(f"QTextBrowser {{ background: {theme.READER_BG}; color: {theme.READER_TEXT}; border: none; }}")
        if self.book is not None and self.ci >= 0:
            ci, cur = self.ci, self.current
            self.ci = -1
            self.show_chapter(self.book, ci)
            if cur is not None:
                self.highlight(cur, center=True)

    # ---- layout -------------------------------------------------------------
    def resizeEvent(self, e) -> None:
        # margins first, so the document re-wraps to the new column width in super().resizeEvent.
        # (QAbstractScrollArea also routes the viewport's resize events here, so use self.width().)
        side = max(24, (self.width() - MAX_COLUMN) // 2)
        if side != self._side:
            self._side = side
            self.setViewportMargins(side, 0, side, 0)
        super().resizeEvent(e)

    def set_font(self, family: str, size: int) -> None:
        self.family, self.size = family, size
        if self.book is not None and self.ci >= 0:
            ci, cur = self.ci, self.current
            self.ci = -1
            self.show_chapter(self.book, ci)
            if cur is not None:
                self.highlight(cur, center=True)

    # ---- content --------------------------------------------------------------
    def show_chapter(self, book: Book, ci: int) -> None:
        if book is self.book and ci == self.ci:
            return
        self.book, self.ci = book, ci
        doc = QTextDocument(self)
        base = QFont(self.family)
        base.setPixelSize(int(self.size * 1.25))
        doc.setDefaultFont(base)
        doc.setDocumentMargin(36)
        cur = QTextCursor(doc)
        body_fmt = QTextBlockFormat()
        body_fmt.setLineHeight(160, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        body_fmt.setBottomMargin(self.size * 0.85)
        head_fmt = QTextBlockFormat(body_fmt)
        head_fmt.setTopMargin(self.size * 1.2)
        head_fmt.setBottomMargin(self.size * 1.1)
        body_cf = QTextCharFormat()
        body_cf.setForeground(QColor(theme.READER_TEXT))
        head_cf = QTextCharFormat()
        head_cf.setForeground(QColor("#f1f3f6"))
        head_cf.setFontWeight(QFont.DemiBold)
        head_cf.setFontPointSize(0)
        hf = QFont(self.family)
        hf.setPixelSize(int(self.size * 1.25 * 1.45))
        hf.setWeight(QFont.DemiBold)
        head_cf.setFont(hf)

        chapter = book.chapters[ci]
        para_start: list[int] = []
        first = True
        for para in chapter.paragraphs:
            is_head = para.kind == "h"
            if first:
                cur.setBlockFormat(head_fmt if is_head else body_fmt)
                cur.setBlockCharFormat(head_cf if is_head else body_cf)
                first = False
            else:
                cur.insertBlock(head_fmt if is_head else body_fmt, head_cf if is_head else body_cf)
            para_start.append(cur.position())
            cur.insertText(para.text, head_cf if is_head else body_cf)
        self.setDocument(doc)

        self.ranges.clear()
        self.starts, self.units = [], []
        for u in book.chapter_units(ci):
            _, pi, si = book.units[u]
            s = chapter.paragraphs[pi].sentences[si]
            a, b = para_start[pi] + s.start, para_start[pi] + s.end
            self.ranges[u] = (a, b)
            self.starts.append(a)
            self.units.append(u)
        self.current = None
        self.verticalScrollBar().setValue(0)

    def unit_at(self, pos: int) -> int | None:
        if not self.starts:
            return None
        i = bisect.bisect_right(self.starts, pos) - 1
        if i < 0:
            return self.units[0]
        return self.units[i]

    def highlight(self, u: int, center: bool = False) -> None:
        if u not in self.ranges:
            return
        self.current = u
        a, b = self.ranges[u]
        sel = QTextEdit.ExtraSelection()
        c = QTextCursor(self.document())
        c.setPosition(a)
        c.setPosition(b, QTextCursor.KeepAnchor)
        sel.cursor = c
        fmt = QTextCharFormat()
        fmt.setBackground(theme.HIGHLIGHT)
        fmt.setForeground(theme.HIGHLIGHT_TEXT)
        sel.format = fmt
        self.setExtraSelections([sel])
        QTimer.singleShot(0, lambda: self._ensure_visible(a, b, center))

    def _ensure_visible(self, a: int, b: int, center: bool) -> None:
        c = QTextCursor(self.document())
        c.setPosition(a)
        top = self.cursorRect(c).top()
        c.setPosition(b)
        bottom = self.cursorRect(c).bottom()
        h = self.viewport().height()
        if center or top < h * 0.12 or bottom > h * 0.82:
            sb = self.verticalScrollBar()
            sb.setValue(sb.value() + top - int(h * 0.33))

    # ---- mouse ----------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        super().mouseReleaseEvent(e)
        if e.button() != Qt.LeftButton or self._press is None:
            return
        moved = (e.position().toPoint() - self._press).manhattanLength() > 4
        self._press = None
        if moved or self.textCursor().hasSelection():
            return
        u = self.unit_at(self.cursorForPosition(e.position().toPoint()).position())
        if u is not None:
            self.unitClicked.emit(u)

    def mouseDoubleClickEvent(self, e) -> None:
        u = self.unit_at(self.cursorForPosition(e.position().toPoint()).position())
        if u is not None:
            self.unitActivated.emit(u)

    def _menu(self, pos) -> None:
        u = self.unit_at(self.cursorForPosition(pos).position())
        m = QMenu(self)
        if u is not None:
            m.addAction("Read from here", lambda: self.unitActivated.emit(u))
        if self.textCursor().hasSelection():
            m.addAction("Copy", self.copy)
        if not m.isEmpty():
            m.exec(self.viewport().mapToGlobal(pos))
