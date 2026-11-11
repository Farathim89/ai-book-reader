"""The paintbrush button's panel (like ABS-DA's): small previews of every colour theme and
bookshelf, and how titles show. A click switches it live."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from . import shelf as shelf_mod
from . import theme

TILE_W, TILE_H = 112, 60
BOOK_COLOURS = ["#8e3b46", "#2f6f8f", "#c7a24a", "#3d7a4f", "#6b4f9e", "#b8653a", "#1f4e5f"]


def theme_preview(name: str) -> QPixmap:
    """A tiny drawing of the app in this theme: top bar, side menu, cards, player."""
    appbar, sidebar, bg, surface, surface2, _s3, border, text, muted, _faint, accent, green, _gh = theme.THEMES[name]
    pm = QPixmap(TILE_W, TILE_H)
    pm.fill(QColor(bg))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, TILE_W, 10, QColor(appbar))
    for k in range(3):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(muted))
        p.drawEllipse(QRectF(5 + k * 6, 3.5, 3, 3))
    p.fillRect(0, 10, 16, TILE_H - 10, QColor(sidebar))
    p.fillRect(0, 15, 3, 7, QColor(accent))
    for k in range(3):
        p.fillRect(5, 16 + k * 9, 7, 3, QColor(muted if k else accent))
    for k in range(4):                                     # covers on a shelf
        p.fillRect(22 + k * 21, 16, 16, 22, QColor(surface2))
        p.fillRect(22 + k * 21, 16, 16, 4, QColor(BOOK_COLOURS[k]))
    p.fillRect(22, 30, 16, 2, QColor(accent))
    p.fillRect(16, TILE_H - 13, TILE_W - 16, 13, QColor(surface))
    p.fillRect(16, TILE_H - 13, TILE_W - 16, 1, QColor(border))
    p.setBrush(QColor(green))
    p.drawEllipse(QRectF(TILE_W / 2 + 4, TILE_H - 11.5, 10, 10))
    p.fillRect(24, TILE_H - 7, 30, 2, QColor(accent))
    p.fillRect(54, TILE_H - 7, 14, 2, QColor(text))
    p.end()
    return pm


def shelf_preview(style: str) -> QPixmap:
    """The bookshelf: wall, one plank and a few books standing on it."""
    pm = QPixmap(TILE_W, TILE_H)
    p = QPainter(pm)
    p.drawTiledPixmap(0, 0, TILE_W, TILE_H, shelf_mod.wall_pixmap(style), 40, 40)
    plank_y = 44
    p.drawTiledPixmap(0, plank_y, TILE_W, 9, shelf_mod.plank_pixmap(style), 0, 10)
    p.fillRect(0, plank_y, TILE_W, 1, QColor(255, 235, 200, 70))
    p.fillRect(0, plank_y + 9, TILE_W, 4, QColor(0, 0, 0, 90))
    x = 12
    for k, h in enumerate((30, 26, 33, 28, 31, 24)):
        w = 12 if k % 3 else 14
        p.fillRect(x + 2, plank_y - h + 2, w, h, QColor(0, 0, 0, 80))
        p.fillRect(x, plank_y - h, w, h, QColor(BOOK_COLOURS[k]))
        p.fillRect(x, plank_y - h + 5, w, 2, QColor(255, 255, 255, 60))
        x += w + 4
    p.end()
    return pm


class _Tile(QToolButton):
    def __init__(self, pixmap: QPixmap, label: str, on: bool):
        super().__init__()
        self.setObjectName("LooksTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("on", on)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIcon(pixmap)
        self.setIconSize(pixmap.size())
        self.setText(label)
        self.setFixedSize(TILE_W + 12, TILE_H + 34)


class LooksPanel(QFrame):
    """Popup under the paintbrush button. on_change(key, value) applies and saves the choice."""

    def __init__(self, parent: QWidget, settings, on_change: Callable[[str, str], None]):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("LooksPanel")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.settings = settings
        self.on_change = on_change
        self.setStyleSheet(f"""
            QFrame#LooksPanel {{ background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}
            QLabel {{ background: transparent; }}
            QLabel#Head {{ color: {theme.TEXT}; font-size: 11pt; font-weight: 600; padding: 4px 2px 2px 2px; }}
            QToolButton#LooksTile {{ background: transparent; border: 2px solid transparent; border-radius: 8px;
                                     color: {theme.MUTED}; padding: 3px; font-size: 9pt; }}
            QToolButton#LooksTile:hover {{ background: {theme.SURFACE3}; color: {theme.TEXT}; }}
            QToolButton#LooksTile[on="true"] {{ border-color: {theme.ACCENT}; color: {theme.ACCENT}; font-weight: 600; }}
            QPushButton#Chip {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 14px;
                                color: {theme.TEXT}; padding: 5px 14px; }}
            QPushButton#Chip:hover {{ border-color: {theme.MUTED}; }}
            QPushButton#Chip[on="true"] {{ border: 2px solid {theme.ACCENT}; color: {theme.ACCENT}; font-weight: 600; }}""")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(6)
        lay.addWidget(self._head("Colour theme"))
        lay.addLayout(self._grid([(n, theme_preview(n)) for n in theme.THEMES], "theme",
                                 settings.get("theme", "Audiobookshelf")))
        lay.addWidget(self._head("Bookshelf"))
        lay.addLayout(self._grid([(n, shelf_preview(n)) for n in shelf_mod.SHELF_STYLES], "shelf_style",
                                 settings.get("shelf_style", "Walnut")))
        lay.addWidget(self._head("Book titles"))
        row = QHBoxLayout()
        row.setSpacing(8)
        current = settings.get("title_bar", "under")
        for key, label in (("off", "Off"), ("cover", "On the cover"), ("under", "Under the book")):
            b = QPushButton(label)
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("on", key == current)
            b.clicked.connect(lambda _=False, k=key: self._pick("title_bar", k))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

    @staticmethod
    def _head(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Head")
        return lab

    def _grid(self, items: list[tuple[str, QPixmap]], key: str, current: str) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(4)
        for n, (name, pm) in enumerate(items):
            t = _Tile(pm, name, name == current)
            t.clicked.connect(lambda _=False, v=name: self._pick(key, v))
            grid.addWidget(t, n // 5, n % 5)
        return grid

    def _pick(self, key: str, value: str) -> None:
        pos = self.pos()
        parent, settings, on_change = self.parentWidget(), self.settings, self.on_change
        self.close()
        on_change(key, value)
        again = LooksPanel(parent, settings, on_change)        # redrawn in the new colours
        again.adjustSize()
        again.move(pos)
        again.show()

    def show_under(self, button: QWidget) -> None:
        self.adjustSize()
        screen = button.screen().availableGeometry()
        pos = button.mapToGlobal(QPoint(button.width() - self.width(), button.height() + 6))
        self.move(max(screen.left() + 8, min(pos.x(), screen.right() - self.width() - 8)), pos.y())
        self.show()
