"""ABS-style bottom player: always visible while a book is loaded, on every page."""
from __future__ import annotations

import bisect

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from ..library.libraries import fmt_clock
from . import theme
from .voice_picker import VoiceCombo
from .shelf import paint_cover
from .widgets import IconButton, muted

SPEEDS = [0.75, 0.85, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0]
SLEEP = [("Off", None), ("5 minutes", 5), ("10 minutes", 10), ("15 minutes", 15), ("30 minutes", 30),
         ("45 minutes", 45), ("60 minutes", 60), ("90 minutes", 90), ("End of chapter", "chapter")]


class Thumb(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(54, 80)
        self.path, self.title = None, ""
        self.setCursor(Qt.PointingHandCursor)

    def set_cover(self, path, title) -> None:
        self.path, self.title = path, title
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_cover(p, QRectF(0, 0, self.width(), self.height()), self.path, self.title, radius=3)

    def mouseReleaseEvent(self, e) -> None:
        self.clicked.emit()


class Track(QWidget):
    """Book progress with chapter ticks. Click or drag to jump."""
    seek = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.total = 0
        self.value = 0
        self.ticks: list[int] = []
        self.titles: list[str] = []
        self._drag: int | None = None
        self._hover = False

    def set_data(self, total: int, ticks: list[int], titles: list[str]) -> None:
        self.total, self.ticks, self.titles = total, ticks, titles
        self.update()

    def set_value(self, v: int) -> None:
        if self._drag is None:
            self.value = v
            self.update()

    def _bar(self) -> QRectF:
        return QRectF(0, self.height() / 2 - 3, self.width(), 6)

    def _frac(self, v: int) -> float:
        return v / max(1, self.total - 1)

    def _unit_at(self, x: float) -> int:
        return int(round(max(0.0, min(1.0, x / max(1, self.width()))) * max(0, self.total - 1)))

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bar = self._bar()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#4a4a4a"))
        p.drawRoundedRect(bar, 3, 3)
        v = self._drag if self._drag is not None else self.value
        if self.total:
            filled = QRectF(bar.left(), bar.top(), bar.width() * self._frac(v), bar.height())
            p.setBrush(QColor(theme.YELLOW))
            p.drawRoundedRect(filled, 3, 3)
            p.setPen(QPen(QColor(20, 20, 20, 200), 1.5))
            for t in self.ticks[1:]:
                x = bar.width() * self._frac(t)
                p.drawLine(int(x), int(bar.top()), int(x), int(bar.bottom()))
            if self._hover or self._drag is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.TEXT))
                x = bar.width() * self._frac(v)
                p.drawEllipse(QRectF(x - 6, bar.center().y() - 6, 12, 12))

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseMoveEvent(self, e) -> None:
        u = self._unit_at(e.position().x())
        if self.ticks and self.titles:
            ci = max(0, bisect.bisect_right(self.ticks, u) - 1)
            self.setToolTip(self.titles[ci] if ci < len(self.titles) else "")
        if self._drag is not None:
            self._drag = u
            self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.total:
            self._drag = self._unit_at(e.position().x())
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            u, self._drag = self._drag, None
            self.value = u
            self.update()
            self.seek.emit(u)


class PlayerBar(QFrame):
    playClicked = Signal()
    stepClicked = Signal(int)
    chapterClicked = Signal(int)
    chapterChosen = Signal(int)
    seek = Signal(int)
    voiceChanged = Signal(str)
    speedChanged = Signal(float)
    volumeChanged = Signal(float)
    sleepChosen = Signal(object)        # None / minutes / "chapter"
    openReader = Signal()
    openBook = Signal()
    closeClicked = Signal()
    charactersToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(100)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 8)
        outer.setSpacing(14)

        # left: cover + text
        left = QHBoxLayout()
        left.setSpacing(12)
        self.thumb = Thumb()
        self.thumb.clicked.connect(self.openBook)
        left.addWidget(self.thumb)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = QLabel("")
        self.title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.title.setCursor(Qt.PointingHandCursor)
        self.title.mouseReleaseEvent = lambda e: self.openBook.emit()
        self.author = muted("")
        self.chapter = QLabel("")
        self.chapter.setStyleSheet(f"color: {theme.FAINT}; font-size: 9pt;")
        for w in (self.title, self.author, self.chapter):
            w.setMaximumWidth(300)
            text.addWidget(w)
        text.addStretch(1)
        left.addLayout(text)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(370)
        outer.addWidget(lw)

        # centre: controls + track
        center = QVBoxLayout()
        center.setSpacing(2)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addStretch(1)
        self.sleep = QToolButton()
        self.sleep.setToolTip("Sleep timer")
        self.sleep.setPopupMode(QToolButton.InstantPopup)
        self.sleep.setCursor(Qt.PointingHandCursor)
        self._sleep_menu = QMenu(self)
        for label, val in SLEEP:
            act = QAction(label, self._sleep_menu)
            act.triggered.connect(lambda _=False, v=val: self.sleepChosen.emit(v))
            self._sleep_menu.addAction(act)
        self.sleep.setMenu(self._sleep_menu)
        self.set_sleep_label("")
        controls.addWidget(self.sleep)
        self.prev_ch = IconButton("prev", "Previous chapter (Ctrl+Left)", 17)
        self.prev_s = IconButton("rewind", "Previous sentence (Left)", 17)
        self.play = QToolButton()
        self.play.setObjectName("PlayButton")
        theme.use_icon_font(self.play, 18)
        self.play.setFixedSize(44, 44)
        self.play.setCursor(Qt.PointingHandCursor)
        self.next_s = IconButton("forward", "Next sentence (Right)", 17)
        self.next_ch = IconButton("next", "Next chapter (Ctrl+Right)", 17)
        self.speed = QToolButton()
        self.speed.setPopupMode(QToolButton.InstantPopup)
        self.speed.setToolTip("Reading speed")
        self.speed.setCursor(Qt.PointingHandCursor)
        self.speed.setStyleSheet("font-size: 10.5pt; font-weight: 600; padding: 4px 8px;")
        sm = QMenu(self)
        for s in SPEEDS:
            a = QAction(f"{s:g}×", sm)
            a.triggered.connect(lambda _=False, v=s: (self.set_speed(v), self.speedChanged.emit(v)))
            sm.addAction(a)
        self.speed.setMenu(sm)
        for w in (self.prev_ch, self.prev_s, self.play, self.next_s, self.next_ch, self.speed):
            controls.addWidget(w)
        controls.addStretch(1)
        center.addLayout(controls)
        track_row = QHBoxLayout()
        track_row.setSpacing(10)
        self.elapsed = muted("0:00:00")
        self.remaining = muted("")
        for lab in (self.elapsed, self.remaining):
            lab.setStyleSheet(f"color: {theme.MUTED}; font-size: 9pt;")
            lab.setMinimumWidth(64)
        self.remaining.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.track = Track()
        track_row.addWidget(self.elapsed)
        track_row.addWidget(self.track, 1)
        track_row.addWidget(self.remaining)
        center.addLayout(track_row)
        outer.addLayout(center, 1)

        # right: voice, characters, chapters, reader, volume, close
        right = QHBoxLayout()
        right.setSpacing(4)
        self.voice = VoiceCombo()
        self.voice.setMinimumWidth(170)
        self.voice.setMaximumWidth(220)
        self.voice.setToolTip("Narrator voice for this book")
        right.addWidget(self.voice)
        self.chars = IconButton("people", "Character voices on/off", 15)
        self.chars.setCheckable(True)
        self.chars.toggled.connect(self.charactersToggled)
        self.chapters_btn = IconButton("list", "Chapters", 15)
        self.chapters_btn.setPopupMode(QToolButton.InstantPopup)
        self._chapters_menu = QMenu(self)
        self.chapters_btn.setMenu(self._chapters_menu)
        self.reader_btn = IconButton("read", "Open the text (reader)", 15)
        self.reader_btn.clicked.connect(self.openReader)
        vol_icon = QLabel(theme.I["volume"])
        theme.use_icon_font(vol_icon, 14, f"color: {theme.MUTED};")
        self.vol_icon = vol_icon
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(80)
        self.close_btn = IconButton("close", "Stop and close the player", 13)
        self.close_btn.clicked.connect(self.closeClicked)
        for w in (self.chars, self.chapters_btn, self.reader_btn, vol_icon, self.volume, self.close_btn):
            right.addWidget(w)
        outer.addLayout(right)

        self.play.clicked.connect(self.playClicked)
        self.prev_s.clicked.connect(lambda: self.stepClicked.emit(-1))
        self.next_s.clicked.connect(lambda: self.stepClicked.emit(1))
        self.prev_ch.clicked.connect(lambda: self.chapterClicked.emit(-1))
        self.next_ch.clicked.connect(lambda: self.chapterClicked.emit(1))
        self.track.seek.connect(self.seek)
        self.voice.activated.connect(lambda i: self.voiceChanged.emit(self.voice.itemData(i)))
        self.volume.valueChanged.connect(lambda v: self.volumeChanged.emit(v / 100))
        self.set_state("stopped")
        self.set_speed(1.0)

    def restyle(self) -> None:
        """Re-apply theme colours set on individual widgets."""
        self.chapter.setStyleSheet(f"color: {theme.FAINT}; font-size: 9pt;")
        for lab in (self.elapsed, self.remaining):
            lab.setStyleSheet(f"color: {theme.MUTED}; font-size: 9pt;")
        theme.use_icon_font(self.vol_icon, 14, f"color: {theme.MUTED};")
        if theme.I["moon"] + "  " in self.sleep.text():
            self.set_sleep_label(self.sleep.text().split("  ", 1)[1])
        self.track.update()

    # ---- setters ----------------------------------------------------------------
    def set_book(self, title: str, author: str, cover: str | None, chapter_titles: list[str], ticks: list[int],
                 total: int) -> None:
        self.title.setText(self.title.fontMetrics().elidedText(title, Qt.ElideRight, 300))
        self.title.setToolTip(title)
        self.author.setText(author)
        self.thumb.set_cover(cover, title)
        self.track.set_data(total, ticks, chapter_titles)
        self._chapters_menu.clear()
        for ci, t in enumerate(chapter_titles):
            a = QAction(f"{ci + 1}. {t}"[:80], self._chapters_menu)
            a.triggered.connect(lambda _=False, c=ci: self.chapterChosen.emit(c))
            self._chapters_menu.addAction(a)

    def set_voices(self, voices, current: str) -> None:
        self.voice.blockSignals(True)
        self.voice.clear()
        for v in voices:            # the list opens in columns: female | male, cloned voices below
            self.voice.addItem(v.label, v.id)
        i = self.voice.findData(current)
        self.voice.setCurrentIndex(max(i, 0))
        self.voice.blockSignals(False)

    def set_characters(self, available: bool, on: bool) -> None:
        self.chars.blockSignals(True)
        self.chars.setEnabled(available)
        self.chars.setChecked(on and available)
        self.chars.setToolTip("Character voices on/off" if available else
                              "No character voices yet (book page ▸ Characters ▸ Find characters)")
        self.chars.blockSignals(False)

    def set_speed(self, speed: float) -> None:
        self.speed.setText(f"{speed:g}×")

    def set_volume(self, v: float) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(int(v * 100))
        self.volume.blockSignals(False)

    def set_sleep_label(self, text: str) -> None:
        if text:
            self.sleep.setText(f"{theme.I['moon']}  {text}")
            self.sleep.setStyleSheet(f'font-family: "{theme.ICON_FONT}", "Segoe UI"; font-size: 14px; color: {theme.YELLOW};')
        else:
            self.sleep.setText(theme.I["moon"])
            self.sleep.setStyleSheet(f'font-family: "{theme.ICON_FONT}"; font-size: 15px;')

    def set_state(self, state: str) -> None:
        playing = state in ("playing", "loading")
        self.play.setText(theme.I["pause"] if playing else theme.I["play"])
        self.play.setToolTip("Pause (Space)" if playing else "Play (Space)")

    def set_position(self, unit: int, chapter_title: str, chapter: int, chapters: int,
                     elapsed: float, remaining: float) -> None:
        self.track.set_value(unit)
        self.chapter.setText(self.chapter.fontMetrics().elidedText(
            f"Chapter {chapter + 1}/{chapters} · {chapter_title}", Qt.ElideRight, 300))
        self.elapsed.setText(fmt_clock(elapsed))
        self.remaining.setText("-" + fmt_clock(remaining))
