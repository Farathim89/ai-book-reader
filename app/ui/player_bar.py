"""Transport controls: chapter/sentence skip, play/pause, position, narrator voice, speed, volume."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QSlider, QToolButton

from . import theme
from .widgets import Bar, IconButton, muted, vline

SPEEDS = [0.75, 0.85, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0]


class PlayerBar(Bar):
    playClicked = Signal()
    stepClicked = Signal(int)        # -1 / +1 sentence
    chapterClicked = Signal(int)     # -1 / +1 chapter
    seek = Signal(int)               # unit
    voiceChanged = Signal(str)
    speedChanged = Signal(float)
    volumeChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__("PlayerBar", parent, margins=(14, 8, 18, 8))
        self.lay.setSpacing(4)
        self.prev_ch = self.add(IconButton("prev", "Previous chapter (Ctrl+Left)", 15))
        self.prev_s = self.add(IconButton("rewind", "Previous sentence (Left)", 15))
        self.play = QToolButton()
        self.play.setObjectName("PlayButton")
        theme.use_icon_font(self.play, 17)
        self.play.setFixedSize(42, 42)
        self.play.setCursor(Qt.PointingHandCursor)
        self.play.setToolTip("Play / pause (Space)")
        self.add(self.play)
        self.next_s = self.add(IconButton("forward", "Next sentence (Right)", 15))
        self.next_ch = self.add(IconButton("next", "Next chapter (Ctrl+Right)", 15))
        self.lay.addSpacing(10)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimumWidth(160)
        self.slider.setRange(0, 0)
        self.add(self.slider, 1)
        self.pos_label = muted("")
        self.pos_label.setMinimumWidth(150)
        self.add(self.pos_label)
        self.lay.addSpacing(6)
        self.add(vline())
        self.lay.addSpacing(6)

        mic = QLabel(theme.I["mic"])
        theme.use_icon_font(mic, 14, f"color: {theme.MUTED};")
        mic.setToolTip("Narrator voice")
        self.add(mic)
        self.voice = QComboBox()
        self.voice.setMinimumWidth(210)
        self.voice.setToolTip("Narrator voice for this book")
        self.add(self.voice)
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s:g}×", s)
        self.speed.setToolTip("Reading speed")
        self.add(self.speed)
        vol = QLabel(theme.I["volume"])
        theme.use_icon_font(vol, 14, f"color: {theme.MUTED};")
        self.lay.addSpacing(6)
        self.add(vol)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(90)
        self.add(self.volume)

        self.play.clicked.connect(self.playClicked)
        self.prev_s.clicked.connect(lambda: self.stepClicked.emit(-1))
        self.next_s.clicked.connect(lambda: self.stepClicked.emit(1))
        self.prev_ch.clicked.connect(lambda: self.chapterClicked.emit(-1))
        self.next_ch.clicked.connect(lambda: self.chapterClicked.emit(1))
        self.slider.sliderReleased.connect(lambda: self.seek.emit(self.slider.value()))
        self.voice.activated.connect(lambda i: self.voiceChanged.emit(self.voice.itemData(i)))
        self.speed.activated.connect(lambda i: self.speedChanged.emit(float(self.speed.itemData(i))))
        self.volume.valueChanged.connect(lambda v: self.volumeChanged.emit(v / 100))
        self.set_state("stopped")

    def set_voices(self, voices, current: str) -> None:
        self.voice.blockSignals(True)
        self.voice.clear()
        groups = [("Kokoro voices", [v for v in voices if v.engine == "kokoro"]),
                  ("Cloned / Chatterbox", [v for v in voices if v.engine == "chatterbox"])]
        for title, vs in groups:
            if not vs:
                continue
            self.voice.addItem(f"— {title} —", None)
            idx = self.voice.count() - 1
            self.voice.model().item(idx).setEnabled(False)
            for v in vs:
                self.voice.addItem(v.label, v.id)
        i = self.voice.findData(current)
        self.voice.setCurrentIndex(i if i >= 0 else 1)
        self.voice.blockSignals(False)

    def set_speed(self, speed: float) -> None:
        best = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - speed))
        self.speed.setCurrentIndex(best)

    def set_volume(self, v: float) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(int(v * 100))
        self.volume.blockSignals(False)

    def set_state(self, state: str) -> None:
        playing = state in ("playing", "loading")
        self.play.setText(theme.I["pause"] if playing else theme.I["play"])
        self.play.setToolTip("Pause (Space)" if playing else "Play (Space)")

    def set_position(self, unit: int, total: int, chapter: int, chapters: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setRange(0, max(0, total - 1))
            self.slider.setValue(unit)
        pct = int(unit / max(1, total - 1) * 100) if total > 1 else 0
        self.pos_label.setText(f"Chapter {chapter + 1} of {chapters}  ·  {pct}%")
