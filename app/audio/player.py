"""Gapless audio output: a queue of sentence clips played through one sounddevice stream.

The audio callback runs on PortAudio's thread; it only copies samples and posts
("start", key) / ("end", key) events to a queue that the UI polls."""
from __future__ import annotations

import collections
import queue
import threading
from dataclasses import dataclass

import numpy as np

SR = 24000


@dataclass
class Clip:
    key: object
    data: np.ndarray
    pos: int = 0
    started: bool = False


class AudioPlayer:
    def __init__(self, samplerate: int = SR):
        self.sr = samplerate
        self._clips: collections.deque[Clip] = collections.deque()
        self._lock = threading.Lock()
        self._paused = True
        self._stream = None
        self._starved = False
        self.volume = 1.0
        self.events: queue.Queue = queue.Queue()
        self.error: str | None = None

    # ---- stream -------------------------------------------------------------
    def _ensure_stream(self) -> bool:
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd
            self._stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32",
                                           callback=self._callback, latency="high")
            self._stream.start()
            self.error = None
            return True
        except Exception as e:  # no output device etc.
            self._stream = None
            self.error = f"Audio output failed: {e}"
            return False

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _callback(self, outdata, frames, time_info, status) -> None:
        out = outdata[:, 0]
        out.fill(0.0)
        if self._paused:
            return
        n = 0
        with self._lock:
            while n < frames and self._clips:
                c = self._clips[0]
                if not c.started:
                    c.started = True
                    self.events.put(("start", c.key))
                take = min(frames - n, c.data.size - c.pos)
                if take > 0:
                    out[n:n + take] = c.data[c.pos:c.pos + take]
                    c.pos += take
                    n += take
                if c.pos >= c.data.size:
                    self._clips.popleft()
                    self.events.put(("end", c.key))
            if n < frames and not self._clips and not self._starved:
                self._starved = True
                self.events.put(("starved", None))
        if self.volume != 1.0:
            out *= self.volume

    # ---- control ------------------------------------------------------------
    def enqueue(self, key, data: np.ndarray) -> None:
        with self._lock:
            self._clips.append(Clip(key, np.ascontiguousarray(data, dtype=np.float32)))
            self._starved = False

    def clear(self) -> None:
        with self._lock:
            self._clips.clear()
            self._starved = False
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    def play(self) -> bool:
        ok = self._ensure_stream()
        self._paused = False
        return ok

    def pause(self) -> None:
        self._paused = True

    @property
    def paused(self) -> bool:
        return self._paused

    def buffered_seconds(self) -> float:
        with self._lock:
            return sum(c.data.size - c.pos for c in self._clips) / self.sr

    def queued_keys(self) -> list:
        with self._lock:
            return [c.key for c in self._clips]
