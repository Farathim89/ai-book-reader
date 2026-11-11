"""Voice drop-down whose list opens as two columns - female | male - with the cloned / Chatterbox
voices underneath. It is a normal QComboBox otherwise (items, itemData, findData, activated)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme

SEP = "·"


def split_label(label: str) -> tuple[str, str]:
    """'Heart  ·  US female' -> ('Heart', 'US female')."""
    name, _, detail = label.partition(SEP)
    return name.strip(), detail.strip()


def group_of(label: str, data) -> str:
    """'female', 'male', 'other' (cloned / Chatterbox) or 'special' (e.g. 'Narrator voice')."""
    if not data:
        return "special"
    words = split_label(label)[1].lower().split()
    if "female" in words:
        return "female"
    if "male" in words:
        return "male"
    return "other"


class _VoicePopup(QFrame):
    COL_W = 200

    def __init__(self, combo: "VoiceCombo"):
        super().__init__(combo, Qt.Popup | Qt.FramelessWindowHint)
        self.combo = combo
        self.setObjectName("VoicePopup")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(f"""
            QFrame#VoicePopup {{ background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}
            QLabel {{ background: transparent; }}
            QLabel#Head {{ color: {theme.MUTED}; font-size: 9pt; font-weight: 600; padding: 2px 10px 4px 10px; }}
            QLabel#Name {{ color: {theme.TEXT}; }}
            QLabel#Detail {{ color: {theme.FAINT}; }}
            QPushButton {{ background: transparent; border: none; border-radius: 5px; }}
            QPushButton:hover {{ background: {theme.SURFACE3}; }}
            QPushButton[current="true"] {{ background: {theme.ACCENT_DIM}; }}
            QPushButton[current="true"] QLabel#Name {{ color: {theme.ACCENT_HOVER}; font-weight: 600; }}
            QFrame#Rule {{ background: {theme.BORDER}; max-height: 1px; min-height: 1px; border: none; }}""")
        groups: dict[str, list[tuple[int, str, str]]] = {"special": [], "female": [], "male": [], "other": []}
        for i in range(combo.count()):
            item = combo.model().item(i) if hasattr(combo.model(), "item") else None
            if item is not None and not item.isEnabled():
                continue                                   # old-style "— Kokoro voices —" headers
            label, data = combo.itemText(i), combo.itemData(i)
            name, detail = split_label(label)
            g = group_of(label, data)
            if g in ("female", "male"):
                detail = " ".join(w for w in detail.split() if w.lower() not in ("female", "male"))
            groups[g].append((i, name, detail))
        for g in ("female", "male"):                       # US voices first, then UK, list order kept
            groups[g].sort(key=lambda t: (t[2] != "US", t[2]))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(2)
        for i, name, detail in groups["special"]:
            lay.addWidget(self._button(i, name, detail, wide=True))
        if groups["special"]:
            lay.addWidget(self._rule())
        cols = QHBoxLayout()
        cols.setSpacing(6)
        for g, title in (("female", "Female"), ("male", "Male")):
            if not groups[g]:
                continue
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(self._head(f"{title.upper()}  ({len(groups[g])})"))
            for i, name, detail in groups[g]:
                col.addWidget(self._button(i, name, detail))
            col.addStretch(1)
            cols.addLayout(col)
        lay.addLayout(cols)
        if groups["other"]:
            lay.addWidget(self._rule())
            lay.addWidget(self._head("CLONED / CHATTERBOX"))
            grid = QGridLayout()
            grid.setSpacing(1)
            for n, (i, name, detail) in enumerate(groups["other"]):
                grid.addWidget(self._button(i, name, detail), n // 2, n % 2, Qt.AlignLeft)
            lay.addLayout(grid)

    @staticmethod
    def _head(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Head")
        return lab

    def _rule(self) -> QWidget:
        r = QFrame()
        r.setObjectName("Rule")
        return r

    def _button(self, index: int, name: str, detail: str, wide: bool = False) -> QPushButton:
        """Name on the left, accent / detail dimmer on the right."""
        b = QPushButton()
        b.setToolTip(self.combo.itemText(index))
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("current", index == self.combo.currentIndex())
        b.setFixedSize(self.COL_W * 2 + 6 if wide else self.COL_W, 30)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QHBoxLayout(b)
        row.setContentsMargins(10, 0, 10, 0)
        for text, obj in ((name, "Name"), (detail, "Detail")):
            lab = QLabel(text)
            lab.setObjectName(obj)
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(lab)
            if obj == "Name":
                row.addStretch(1)
        b.clicked.connect(lambda _=False, i=index: self._pick(i))
        return b

    def _pick(self, index: int) -> None:
        self.close()
        self.combo.setCurrentIndex(index)
        self.combo.activated.emit(index)

    def show_for(self, combo: QComboBox) -> None:
        self.adjustSize()
        screen = (combo.screen() or self.screen()).availableGeometry()
        below = combo.mapToGlobal(QPoint(0, combo.height() + 2))
        above = combo.mapToGlobal(QPoint(0, -self.height() - 2))
        pos = below if below.y() + self.height() <= screen.bottom() else above
        x = min(max(screen.left(), pos.x() + combo.width() - self.width()), screen.right() - self.width())
        self.move(x, max(screen.top(), pos.y()))
        self.show()
        current = [b for b in self.findChildren(QPushButton) if b.property("current")]
        (current[0] if current else self).setFocus()


class VoiceCombo(QComboBox):
    def showPopup(self) -> None:
        if self.count():
            _VoicePopup(self).show_for(self)
