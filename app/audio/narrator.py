"""Read-aloud controller: generates sentence audio ahead of playback on a worker thread,
feeds the player, and tells the UI which sentence is being spoken."""
from __future__ import annotations

import queue
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..formats.model import Book
from ..tts.planner import VoicePlanner
from ..tts.service import TTSService, silence
from .player import AudioPlayer


class Narrator(QObject):
    unitStarted = Signal(int)       # sentence now being spoken (or selected)
    stateChanged = Signal(str)      # "stopped" | "loading" | "playing" | "paused"
    status = Signal(str)
    error = Signal(str)
    finished = Signal()

    AHEAD_SECONDS = 25.0

    def __init__(self, tts: TTSService, parent=None):
        super().__init__(parent)
        self.tts = tts
        self.player = AudioPlayer(tts.sample_rate)
        self.book: Book | None = None
        self.planner: VoicePlanner | None = None
        self.current = 0
        self.state = "stopped"
        self.speed = 1.0
        self._epoch = 0
        self._want: tuple[int, int] | None = None
        self._cv = threading.Condition()
        self._msgs: queue.Queue = queue.Queue()
        self._shutdown = False
        self._generating = False
        self._thread = threading.Thread(target=self._worker, name="narrator", daemon=True)
        self._thread.start()
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ---- setup --------------------------------------------------------------
    def set_book(self, book: Book | None, planner: VoicePlanner | None, position: int = 0) -> None:
        self.stop()
        self.book = book
        self.planner = planner
        n = book.unit_count() if book else 0
        self.current = min(max(0, position), max(0, n - 1))

    def set_planner(self, planner: VoicePlanner) -> None:
        self.planner = planner
        self._refresh()

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        self._refresh()

    def set_volume(self, v: float) -> None:
        self.player.volume = max(0.0, min(1.5, v))

    def preload(self) -> None:
        """Load the voice models in the background so the first Play starts quickly."""
        if not self.planner:
            return
        voices = [self.tts.resolve(v) for v in self.planner.voices_used()]
        if all(self.tts.is_loaded(v) for v in voices):
            return

        def run():
            try:
                self._msgs.put(("status", "Loading voice model…"))
                self.tts.ensure_loaded(voices)
                self._msgs.put(("status", ""))
            except Exception as e:
                self._msgs.put(("error", f"Could not load the voice model: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def shutdown(self) -> None:
        self._shutdown = True
        with self._cv:
            self._epoch += 1
            self._cv.notify_all()
        self.player.close()

    # ---- transport ----------------------------------------------------------
    def _set_state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            self.stateChanged.emit(s)

    def _restart(self, u: int) -> None:
        with self._cv:
            self._epoch += 1
            self.player.clear()
            self._want = (self._epoch, u)
            self._cv.notify_all()

    def _flush(self) -> None:
        with self._cv:
            self._epoch += 1
            self._want = None
            self.player.clear()

    def _refresh(self) -> None:
        """Settings changed: regenerate from the current sentence."""
        if self.state in ("playing", "loading"):
            self._restart(self.current)
        else:
            self._flush()

    def play(self, from_unit: int | None = None) -> None:
        if not self.book or not self.planner or self.book.unit_count() == 0:
            return
        if from_unit is not None:
            self.current = max(0, min(from_unit, self.book.unit_count() - 1))
        if self.state == "paused" and from_unit is None and self.player.buffered_seconds() > 0:
            self.player.play()
            self._set_state("playing")
            return
        self._restart(self.current)
        if not self.player.play():
            self.error.emit(self.player.error or "Audio output failed")
            return
        self._set_state("loading")

    def pause(self) -> None:
        if self.state in ("playing", "loading"):
            self.player.pause()
            self._set_state("paused")

    def toggle(self) -> None:
        if self.state in ("playing", "loading"):
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._flush()
        self.player.pause()
        self._set_state("stopped")

    def jump(self, u: int) -> None:
        if not self.book or self.book.unit_count() == 0:
            return
        u = max(0, min(u, self.book.unit_count() - 1))
        self.current = u
        self.unitStarted.emit(u)
        if self.state in ("playing", "loading"):
            self._restart(u)
            self._set_state("loading")
        else:
            self._flush()
            if self.state == "paused":
                self._set_state("stopped")

    def step(self, delta: int) -> None:
        self.jump(self.current + delta)

    def chapter_step(self, delta: int) -> None:
        if not self.book or not self.book.units:
            return
        ci = self.book.chapter_of(self.current)
        if delta < 0 and self.current > self.book.chapter_first_unit[ci] + 2:
            target = ci          # restart the current chapter first
        else:
            target = max(0, min(len(self.book.chapters) - 1, ci + delta))
        self.jump(self.book.chapter_first_unit[target])

    # ---- worker ---------------------------------------------------------------
    def _worker(self) -> None:
        while not self._shutdown:
            with self._cv:
                while self._want is None and not self._shutdown:
                    self._cv.wait(0.5)
                if self._shutdown:
                    return
                epoch, u = self._want
            self._generating = True
            try:
                self._generate(epoch, u)
            finally:
                self._generating = False
                with self._cv:
                    if self._want and self._want[0] == epoch:
                        self._want = None

    def _generate(self, epoch: int, u: int) -> None:
        book, planner = self.book, self.planner
        if not book or not planner:
            return
        n = book.unit_count()
        voices = [self.tts.resolve(v) for v in planner.voices_used()]
        if not all(self.tts.is_loaded(v) for v in voices):
            self._msgs.put(("status", "Loading voice model…"))
            try:
                self.tts.ensure_loaded(voices)
            except Exception as e:
                self._msgs.put(("error", f"Could not load the voice model: {e}"))
                return
            self._msgs.put(("status", ""))
        while u < n and epoch == self._epoch and not self._shutdown:
            while (self.player.buffered_seconds() > self.AHEAD_SECONDS and epoch == self._epoch
                   and not self._shutdown):
                time.sleep(0.05)
            if epoch != self._epoch:
                return
            try:
                audio = self.tts.synth_runs(planner.runs(u), self.speed)
            except Exception as e:
                self._msgs.put(("error", f"Speech generation failed: {e}"))
                return
            if audio.size == 0:
                audio = silence(0.05)
            audio = np.concatenate([audio, silence(planner.pause(u) / max(self.speed, 0.5))])
            if epoch != self._epoch:
                return
            self.player.enqueue((epoch, u), audio)
            u += 1

    # ---- UI-thread polling ------------------------------------------------------
    def _poll(self) -> None:
        while True:
            try:
                kind, val = self._msgs.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.emit(val)
            elif kind == "error":
                self.stop()
                self.error.emit(val)
        while True:
            try:
                kind, key = self.player.events.get_nowait()
            except queue.Empty:
                break
            if kind == "start":
                ep, u = key
                if ep == self._epoch:
                    self.current = u
                    if self.state == "loading":
                        self._set_state("playing")
                    self.unitStarted.emit(u)
            elif kind == "end":
                ep, u = key
                if ep == self._epoch and self.book and u >= self.book.unit_count() - 1:
                    self.player.pause()
                    self._set_state("stopped")
                    self.finished.emit()
            elif kind == "starved":
                if self.state == "playing" and (self._generating or self._want is not None):
                    self._set_state("loading")
