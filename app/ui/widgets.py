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


def shape_path(kind: str):
    """Filled media icons on a 24 x 24 grid, drawn as shapes: crisp at any display scale and without the
    red/blue ClearType fringes an icon-font glyph gets on a dark background."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QPainterPath, QPolygonF
    p = QPainterPath()

    def poly(*pts):
        p.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.closeSubpath()
    if kind == "play":
        poly((8, 5), (19, 12), (8, 19))                       # sits right of centre, which looks centred
    elif kind == "pause":
        p.addRoundedRect(QRectF(6, 5, 4, 14), 1, 1)
        p.addRoundedRect(QRectF(14, 5, 4, 14), 1, 1)
    elif kind == "prev":
        p.addRoundedRect(QRectF(5.5, 6, 2.2, 12), 0.8, 0.8)
        poly((18.5, 6), (9.5, 12), (18.5, 18))
    elif kind == "next":
        p.addRoundedRect(QRectF(16.3, 6, 2.2, 12), 0.8, 0.8)
        poly((5.5, 6), (14.5, 12), (5.5, 18))
    elif kind == "rewind":
        poly((11.5, 6.5), (3, 12), (11.5, 17.5))
        poly((20.5, 6.5), (12, 12), (20.5, 17.5))
    elif kind == "forward":
        poly((3.5, 6.5), (12, 12), (3.5, 17.5))
        poly((12.5, 6.5), (21, 12), (12.5, 17.5))
    elif kind == "moon":
        disc, bite = QPainterPath(), QPainterPath()
        disc.addEllipse(QRectF(3.5, 3.5, 17, 17))
        bite.addEllipse(QRectF(9.5, 0.5, 15, 15))
        p = disc.subtracted(bite)
    return p


SHAPES = ("play", "pause", "prev", "next", "rewind", "forward", "moon")


def shape_for_glyph(glyph: str) -> str | None:
    """The shape_path() kind that replaces an icon-font glyph (theme.I), or None for other glyphs."""
    return next((k for k in SHAPES if theme.I.get(k) == glyph), None)


def paint_shape(p, kind: str, x: float, y: float, size: float, color) -> None:
    """Draw a shape_path() icon `size` px square with its top-left at (x, y)."""
    from PySide6.QtGui import QColor, QPainter, QPen
    col = QColor(color)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(x, y)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(col, 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))   # softly rounded corners
    p.setBrush(col)
    p.drawPath(shape_path(kind))
    p.restore()


class ShapeButton(QToolButton):
    """Tool button showing a shape_path() icon (and optional text after it) in the theme's text colour,
    or a fixed colour (the dark icon on the white play circle)."""

    def __init__(self, kind: str, tip: str = "", size: int = 22, color: str | None = None, parent=None):
        super().__init__(parent)
        self.kind, self.px, self.color, self.label, self.label_color = kind, size, color, "", None
        if tip:
            self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(True)

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def set_label(self, text: str, color: str | None = None) -> None:
        """Text after the icon (e.g. the sleep timer's time left)."""
        self.label, self.label_color = text, color
        self.updateGeometry()
        self.update()

    def _label_font(self):
        f = self.font()
        f.setPointSizeF(10)
        f.setWeight(f.Weight.DemiBold)
        return f

    def sizeHint(self) -> QSize:
        from PySide6.QtGui import QFontMetrics
        w = self.px + 18
        if self.label:
            w += 6 + QFontMetrics(self._label_font()).horizontalAdvance(self.label)
        return QSize(w, self.px + 18)

    def paintEvent(self, e) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter
        super().paintEvent(e)                                  # the style's background: hover, pressed, circle
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(self.color or theme.TEXT)
        if not self.isEnabled():
            col.setAlpha(90)
        tw = 0
        if self.label:
            p.setFont(self._label_font())
            tw = 6 + p.fontMetrics().horizontalAdvance(self.label)
        x = (self.width() - self.px - tw) / 2
        paint_shape(p, self.kind, x, (self.height() - self.px) / 2, self.px, col)
        if self.label:
            p.setPen(QColor(self.label_color or col))
            p.drawText(QRectF(x + self.px + 6, 0, tw, self.height()), Qt.AlignLeft | Qt.AlignVCenter, self.label)


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
