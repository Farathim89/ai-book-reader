"""One place that turns text into audio: picks the engine for a voice, splits long
sentences, trims silence, applies speed, and serialises GPU work with a lock."""
from __future__ import annotations

import subprocess
import threading

import numpy as np

from ..formats.textproc import has_speech, speech_text, split_for_tts
from .engines import SR, ChatterboxEngine, KokoroEngine
from .voices import CHATTERBOX_DEFAULT, DEFAULT_VOICE, clone_ref_path, ffmpeg_exe, find_voice


def trim_silence(a: np.ndarray, threshold: float = 0.012, margin: float = 0.03) -> np.ndarray:
    if a.size == 0:
        return a
    idx = np.flatnonzero(np.abs(a) > threshold)
    if idx.size == 0:
        return np.zeros(0, np.float32)
    m = int(margin * SR)
    return a[max(0, idx[0] - m): min(a.size, idx[-1] + m)]


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(max(0.0, seconds) * SR), np.float32)


def time_stretch(a: np.ndarray, speed: float) -> np.ndarray:
    """Change tempo without changing pitch (ffmpeg atempo)."""
    if abs(speed - 1.0) < 0.01 or a.size == 0:
        return a
    exe = ffmpeg_exe()
    if not exe:
        return a
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                        "-i", "pipe:0", "-filter:a", f"atempo={speed:.3f}", "-f", "f32le", "pipe:1"],
                       input=a.astype(np.float32).tobytes(), capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not r.stdout:
        return a
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


class TTSService:
    sample_rate = SR

    def __init__(self):
        self.kokoro = KokoroEngine()
        self.chatterbox = ChatterboxEngine()
        self.gpu = threading.RLock()

    # ---- engines ------------------------------------------------------------
    def engine_name(self, voice_id: str) -> str:
        return "chatterbox" if voice_id.startswith(("clone:", "chatterbox:")) else "kokoro"

    def is_loaded(self, voice_id: str) -> bool:
        return (self.chatterbox if self.engine_name(voice_id) == "chatterbox" else self.kokoro).loaded

    def ensure_loaded(self, voice_ids) -> None:
        for vid in set(voice_ids):
            with self.gpu:
                if self.engine_name(vid) == "chatterbox":
                    self.chatterbox.load()
                else:
                    self.kokoro.load()

    def resolve(self, voice_id: str | None) -> str:
        """Fall back to the default voice for unknown / deleted voices."""
        if not voice_id:
            return DEFAULT_VOICE
        if voice_id.startswith("clone:") and clone_ref_path(voice_id) is None:
            return DEFAULT_VOICE
        if voice_id.startswith("kokoro:") or voice_id == CHATTERBOX_DEFAULT or voice_id.startswith("clone:"):
            return voice_id if find_voice(voice_id).id == voice_id else DEFAULT_VOICE
        return DEFAULT_VOICE

    # ---- synthesis ------------------------------------------------------------
    def synth(self, voice_id: str, text: str, speed: float = 1.0) -> np.ndarray:
        """Speak one piece of text with one voice. Returns trimmed audio (may be empty)."""
        voice_id = self.resolve(voice_id)
        text = speech_text(text)
        if not has_speech(text):
            return np.zeros(0, np.float32)
        is_cb = self.engine_name(voice_id) == "chatterbox"
        eng = self.chatterbox if is_cb else self.kokoro
        out: list[np.ndarray] = []
        with self.gpu:
            eng.load()
            for chunk in split_for_tts(text, eng.max_chars):
                if not has_speech(chunk):
                    continue
                if is_cb:
                    ref = clone_ref_path(voice_id)
                    a = eng.synth(chunk, voice_id, str(ref) if ref else None)
                else:
                    a = eng.synth(chunk, voice_id.split(":", 1)[1], speed)
                a = trim_silence(a)
                if a.size:
                    if out:
                        out.append(silence(0.12))
                    out.append(a)
        audio = np.concatenate(out) if out else np.zeros(0, np.float32)
        if is_cb:
            audio = time_stretch(audio, speed)
        return audio

    def synth_runs(self, runs: list[tuple[str, str]], speed: float = 1.0) -> np.ndarray:
        """Speak a sentence made of voice runs (narrator + character dialogue)."""
        parts = []
        for vid, text in runs:
            a = self.synth(vid, text, speed)
            if a.size:
                if parts:
                    parts.append(silence(0.06))
                parts.append(a)
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)
