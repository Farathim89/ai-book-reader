"""Library: a grid of book covers with progress."""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QListView, QMenu, QPushButton, QStackedLayout, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget, QLabel)

from . import theme

CARD_W, COVER_H, TEXT_H = 164, 240, 62
BookRole = Qt.UserRole + 1


class LibraryModel(QAbstractListModel):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.rows: list[dict] = []
        self._covers: dict[tuple[int, str], QPixmap] = {}
        self.filter = ""
        self.sort = "recent"

    def reload(self) -> None:
        self.beginResetModel()
        rows = self.library.books()
        f = self.filter.lower().strip()
        if f:
            rows = [r for r in rows if f in (r["title"] or "").lower() or f in (r["author"] or "").lower()]
        if self.sort == "title":
            rows.sort(key=lambda r: (r["title"] or "").lower())
        elif self.sort == "author":
            rows.sort(key=lambda r: ((r["author"] or "~").lower(), (r["title"] or "").lower()))
        elif self.sort == "added":
            rows.sort(key=lambda r: -(r["added"] or 0))
        else:
            rows.sort(key=lambda r: (-(r["opened"] or 0), -(r["added"] or 0)))
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        if role == Qt.DisplayRole:
            return r["title"]
        if role == Qt.ToolTipRole:
            return f"{r['title']}\n{r['author'] or ''}\n{r['path']}"
        if role == BookRole:
            return r
        return None

    def cover(self, r: dict) -> QPixmap | None:
        path = r.get("cover")
        if not path:
            return None
        key = (r["id"], path)
        if key not in self._covers:
            pm = QPixmap(path)
            self._covers[key] = pm.scaled(CARD_W * 2, COVER_H * 2, Qt.KeepAspectRatioByExpanding,
                                          Qt.SmoothTransformation) if not pm.isNull() else None
        return self._covers[key]

    def drop_cover(self, book_id: int) -> None:
        for k in [k for k in self._covers if k[0] == book_id]:
            del self._covers[k]


def _palette_for(text: str) -> tuple[QColor, QColor]:
    h = int(hashlib.md5(text.encode()).hexdigest()[:6], 16)
    hue = h % 360
    return QColor.fromHsl(hue, 90, 70), QColor.fromHsl((hue + 40) % 360, 110, 35)


class BookDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W + 26, COVER_H + TEXT_H + 22)

    def paint(self, p: QPainter, option, index) -> None:
        r: dict = index.data(BookRole)
        model: LibraryModel = index.model()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cell = option.rect
        x = cell.x() + (cell.width() - CARD_W) // 2
        y = cell.y() + 10
        cover_rect = QRectF(x, y, CARD_W, COVER_H)
        hovered = option.state & QStyle.State_MouseOver
        selected = option.state & QStyle.State_Selected

        path = QPainterPath()
        path.addRoundedRect(cover_rect, 8, 8)
        # shadow
        p.fillPath(path.translated(0, 3), QColor(0, 0, 0, 90))
        p.setClipPath(path)
        pm = model.cover(r)
        if pm:
            src = QRectF(pm.rect())
            scale = max(cover_rect.width() / src.width(), cover_rect.height() / src.height())
            w, h = cover_rect.width() / scale, cover_rect.height() / scale
            src = QRectF((src.width() - w) / 2, (src.height() - h) / 2, w, h)
            p.drawPixmap(cover_rect, pm, src)
        else:
            c1, c2 = _palette_for(r["title"] or r["path"])
            g = QLinearGradient(cover_rect.topLeft(), cover_rect.bottomRight())
            g.setColorAt(0, c2)
            g.setColorAt(1, c1.darker(160))
            p.fillRect(cover_rect, QBrush(g))
            p.setPen(QColor(255, 255, 255, 235))
            f = QFont("Georgia")
            f.setPixelSize(19)
            f.setBold(True)
            p.setFont(f)
            p.drawText(cover_rect.adjusted(14, 26, -14, -60), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                       r["title"] or "")
            f2 = QFont("Segoe UI")
            f2.setPixelSize(12)
            p.setFont(f2)
            p.setPen(QColor(255, 255, 255, 180))
            p.drawText(cover_rect.adjusted(14, 0, -14, -18), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignBottom,
                       r["author"] or "")
        # format badge
        badge = (r["fmt"] or "").upper()
        bf = QFont("Segoe UI")
        bf.setPixelSize(10)
        bf.setBold(True)
        p.setFont(bf)
        bw = QFontMetrics(bf).horizontalAdvance(badge) + 12
        brect = QRectF(cover_rect.right() - bw - 6, cover_rect.top() + 6, bw, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(brect, 5, 5)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(brect, Qt.AlignCenter, badge)
        # progress
        prog = float(r.get("progress") or 0.0)
        if prog > 0:
            bar = QRectF(cover_rect.left(), cover_rect.bottom() - 5, cover_rect.width(), 5)
            p.fillRect(bar, QColor(0, 0, 0, 140))
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * min(1.0, prog), bar.height()), QColor(theme.ACCENT))
        p.setClipping(False)
        if hovered or selected:
            p.setPen(QPen(QColor(theme.ACCENT if selected else "#5a6070"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(cover_rect.adjusted(-1, -1, 1, 1), 9, 9)
        # text
        ty = int(cover_rect.bottom()) + 8
        tf = QFont("Segoe UI")
        tf.setPixelSize(13)
        tf.setWeight(QFont.DemiBold)
        p.setFont(tf)
        p.setPen(QColor(theme.TEXT))
        title_rect = QRect(x, ty, CARD_W, 36)
        fm = QFontMetrics(tf)
        title = r["title"] or ""
        lines = _wrap2(title, fm, CARD_W)
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignTop, "\n".join(lines))
        af = QFont("Segoe UI")
        af.setPixelSize(12)
        p.setFont(af)
        p.setPen(QColor(theme.MUTED))
        sub = r["author"] or ""
        if prog > 0:
            sub = f"{sub}  ·  {int(prog * 100)}%" if sub else f"{int(prog * 100)}%"
        p.drawText(QRect(x, ty + 18 * len(lines) + 2, CARD_W, 18), Qt.AlignLeft | Qt.AlignTop,
                   QFontMetrics(af).elidedText(sub, Qt.ElideRight, CARD_W))
        p.restore()


def _wrap2(text: str, fm: QFontMetrics, width: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if fm.horizontalAdvance(test) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
            if len(lines) == 1:
                rest = " ".join(words[words.index(w):])
                lines.append(fm.elidedText(rest, Qt.ElideRight, width))
                return lines
    if cur:
        lines.append(fm.elidedText(cur, Qt.ElideRight, width))
    return lines[:2] or [""]


class LibraryView(QWidget):
    openBook = Signal(int)
    exportBook = Signal(int)
    removeBook = Signal(int)
    revealBook = Signal(int)
    addFolder = Signal()
    addBooks = Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.model = LibraryModel(library, self)
        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(4)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QListView.SingleSelection)
        self.view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(24)
        self.view.setModel(self.model)
        self.view.setItemDelegate(BookDelegate(self.view))
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.activated.connect(lambda idx: self.openBook.emit(idx.data(BookRole)["id"]))
        self.view.setStyleSheet("QListView { padding: 14px; }")

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(2)
        icon = QLabel(theme.I["library"])
        theme.use_icon_font(icon, 54, f"color: {theme.FAINT};")
        icon.setAlignment(Qt.AlignCenter)
        head = QLabel("Your library is empty")
        head.setAlignment(Qt.AlignCenter)
        head.setStyleSheet("font-size: 16pt; font-weight: 600;")
        sub = QLabel("Add a folder with your books (EPUB, PDF, MOBI, AZW3, FB2, DOCX, TXT, RTF, CBZ),\n"
                     "or add single files. The AI voice reads them aloud and can turn them into M4B audiobooks.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setObjectName("Muted")
        b1 = QPushButton("Add a folder")
        b1.setObjectName("Accent")
        b1.clicked.connect(self.addFolder)
        b2 = QPushButton("Add books")
        b2.clicked.connect(self.addBooks)
        for w in (icon, head, sub):
            el.addWidget(w)
        el.addSpacing(12)
        from PySide6.QtWidgets import QHBoxLayout
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        el.addLayout(row)
        el.addStretch(3)

        self.stack = QStackedLayout(self)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty)

    def reload(self) -> None:
        self.model.reload()
        has_books = bool(self.model.library.books())
        self.stack.setCurrentIndex(0 if has_books else 1)

    def set_filter(self, text: str) -> None:
        self.model.filter = text
        self.reload()

    def set_sort(self, key: str) -> None:
        self.model.sort = key
        self.reload()

    def _menu(self, pos) -> None:
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        r = idx.data(BookRole)
        m = QMenu(self)
        m.addAction("Open", lambda: self.openBook.emit(r["id"]))
        m.addAction("Make audiobook (M4B)…", lambda: self.exportBook.emit(r["id"]))
        m.addSeparator()
        m.addAction("Show file in folder", lambda: self.revealBook.emit(r["id"]))
        m.addAction("Remove from library", lambda: self.removeBook.emit(r["id"]))
        m.exec(self.view.viewport().mapToGlobal(pos))
