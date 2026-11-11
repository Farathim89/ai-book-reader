"""Game controllers for TV mode, read with Windows' XInput (Xbox controllers, and PlayStation
controllers through Steam Input / DS4Windows). No extra packages: plain ctypes."""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


BUTTONS = {0x0001: "up", 0x0002: "down", 0x0004: "left", 0x0008: "right", 0x0010: "start", 0x0020: "back",
           0x0100: "lb", 0x0200: "rb", 0x1000: "a", 0x2000: "b", 0x4000: "x", 0x8000: "y"}
REPEATING = {"up", "down", "left", "right"}
STICK = 16000          # of 32767: how far the left stick must move to count as a direction
TRIGGER = 120          # of 255


def _load():
    if sys.platform != "win32":
        return None
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = ctypes.WinDLL(name)
            dll.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
            dll.XInputGetState.restype = wintypes.DWORD
            return dll
        except OSError:
            continue
    return None


def pressed_names(pad: XINPUT_GAMEPAD) -> set[str]:
    names = {n for bit, n in BUTTONS.items() if pad.wButtons & bit}
    if pad.sThumbLX > STICK:
        names.add("right")
    elif pad.sThumbLX < -STICK:
        names.add("left")
    if pad.sThumbLY > STICK:
        names.add("up")
    elif pad.sThumbLY < -STICK:
        names.add("down")
    if pad.bLeftTrigger > TRIGGER:
        names.add("lt")
    if pad.bRightTrigger > TRIGGER:
        names.add("rt")
    return names


class Gamepad(QObject):
    """Polls the first connected controller ~60 times a second while started.
    pressed(name): up/down/left/right (repeat while held), a, b, x, y, lb, rb, lt, rt, start, back.
    volume(step): the right stick up/down, a small step a few times a second."""
    pressed = Signal(str)
    volume = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dll = _load()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.poll)
        self.slot: int | None = None
        self._next_scan = 0.0
        self._held: set[str] = set()
        self._repeat: dict[str, float] = {}
        self._vol_at = 0.0

    @property
    def available(self) -> bool:
        return self.dll is not None

    def start(self) -> None:
        if self.dll is not None:
            self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        self._held.clear()

    def _state(self) -> XINPUT_STATE | None:
        st = XINPUT_STATE()
        now = time.monotonic()
        if self.slot is not None and self.dll.XInputGetState(self.slot, ctypes.byref(st)) == 0:
            return st
        self.slot = None
        if now < self._next_scan:          # asking empty slots is slow: look for a controller every 2 s
            return None
        self._next_scan = now + 2.0
        for i in range(4):
            if self.dll.XInputGetState(i, ctypes.byref(st)) == 0:
                self.slot = i
                return st
        return None

    def poll(self) -> None:
        st = self._state()
        if st is None:
            self._held.clear()
            return
        pad = st.Gamepad
        names = pressed_names(pad)
        now = time.monotonic()
        for n in names - self._held:
            self.pressed.emit(n)
            self._repeat[n] = now + 0.42
        for n in names & self._held & REPEATING:
            if now >= self._repeat.get(n, now + 1):
                self.pressed.emit(n)
                self._repeat[n] = now + 0.11
        self._held = names
        if abs(pad.sThumbRY) > STICK and now >= self._vol_at:
            self.volume.emit(0.05 if pad.sThumbRY > 0 else -0.05)
            self._vol_at = now + 0.15
