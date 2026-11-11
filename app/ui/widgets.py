"""Small reusable widgets."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from . import theme


class IconButton(QToolButton):
    def __init__(self, icon: str, tip: str = "", size: int = 15, parent=None, text: str = ""):
        super().__init__(parent)
        glyph = theme.I.get(icon, icon)
        if text:
            self.setObjectName("TextTool")
            self.setText(text)
            self.setIcon(glyph_icon(glyph, size))
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.setIconSize(QSize(size + 2, size + 2))
        else:
            self.setText(glyph)
            theme.use_icon_font(self, size)
        if tip:
            self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(True)


def glyph_icon(glyph: str, size: int = 16, color: str | None = None):
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(color or theme.TEXT))
    p.setFont(theme.icon_font(int(size * 1.7)))
    p.drawText(pm.rect(), Qt.AlignCenter, glyph)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


class Bar(QFrame):
    """Horizontal bar with consistent padding (top bar / player bar)."""

    def __init__(self, name: str, parent=None, margins=(14, 8, 14, 8)):
        super().__init__(parent)
        self.setObjectName(name)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(6)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.lay.addWidget(w, stretch)
        return w

    def stretch(self) -> None:
        self.lay.addStretch(1)


def muted(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    return lab


def vline() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setFixedHeight(22)
    f.setStyleSheet(f"background: {theme.BORDER};")
    return f
