"""One place that turns text into audio: picks the engine for a voice, splits long
sentences, trims silence, applies speed and loudness, and serialises GPU work with a lock.

Pace and loudness were tuned against human-narrated audiobooks of the same books (Jim Dale's
Harry Potter 1: 155 words/min, -19.7 LUFS; Kiki's Delivery Service: 126 words/min, -17.1 LUFS).
At 1.0x the voices used to read at ~185-190 words/min, 6 dB quieter, with voices up to 6 dB apart."""
from __future__ import annotations

import json
import subprocess
import threading

import numpy as np

from ..formats.textproc import has_speech, speech_text, split_for_tts
from .engines import SR, ChatterboxEngine, KokoroEngine
from .voices import CHATTERBOX_DEFAULT, DEFAULT_VOICE, clone_ref_path, ffmpeg_exe, find_voice

NATURAL_PACE = 0.9          # 1.0x = an audiobook narrator's pace (the raw voices speak ~10% faster)
TARGET_LUFS = -19.0         # audiobook loudness (ACX / Audible range), the same for every voice
CEILING = 0.79              # -2 dBFS: peaks above are limited (AAC adds ~1 dB; audiobook limit is -1 dBTP)
RUN_GAP = 0.15              # between narrator and character inside one sentence ("Hello," | said Harry.)
AUDIO_REV = 2               # bump when the sound changes (exports in progress start over)
CALIBRATION = ("The old house stood at the end of a quiet lane, and every evening the lamps in its "
               "windows were lit one by one, as if someone inside were still waiting for a letter.")


def loudness(a: np.ndarray) -> float | None:
    """Integrated loudness in LUFS (ITU BS.1770 via pyloudnorm; plain gated RMS without it)."""
    if a.size < int(0.5 * SR):
        return None
    try:
        import pyloudnorm
        value = pyloudnorm.Meter(SR).integrated_loudness(a.astype(np.float64))
    except ImportError:
        hop = int(0.4 * SR)
        n = a.size // hop
        power = np.mean(a[: n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1)
        power = power[power > 10 ** (-70 / 10)]
        power = power[power > power.mean() * 0.1] if power.size else power
        value = 10 * np.log10(power.mean()) - 0.691 if power.size else float("-inf")
    return float(value) if np.isfinite(value) else None


def limit(a: np.ndarray, ceiling: float = CEILING, window: float = 0.005) -> np.ndarray:
    """Peak limiter: smooth gain reduction around peaks above the ceiling (no hard clipping)."""
    peak = np.abs(a)
    if a.size == 0 or peak.max() <= ceiling:
        return a
    gain = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9))
    r = max(1, int(window * SR))
    padded = np.pad(gain, r, mode="edge")
    hold = np.lib.stride_tricks.sliding_window_view(padded, 2 * r + 1).min(axis=1)       # radius r
    h = r // 2
    c = np.cumsum(np.pad(hold, (h + 1, h), mode="edge"))
    smooth = (c[2 * h + 1:] - c[: -2 * h - 1]) / (2 * h + 1)                              # radius r/2
    return (a * np.minimum(smooth, 1.0)).astype(np.float32)


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
        self._gains: dict[str, float] | None = None

    # ---- loudness -------------------------------------------------------------
    def _gain_key(self, voice_id: str) -> str:
        ref = clone_ref_path(voice_id) if voice_id.startswith("clone:") else None
        if ref is not None:
            st = ref.stat()
            return f"{voice_id}@{st.st_size}-{int(st.st_mtime)}"      # a re-recorded sample is measured again
        return voice_id

    def _gain_file(self):
        from ..paths import sub
        return sub("voices") / "loudness.json"

    def voice_gain(self, voice_id: str) -> float:
        """dB that bring this voice to TARGET_LUFS; measured once per voice and remembered."""
        if self._gains is None:
            try:
                self._gains = json.loads(self._gain_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._gains = {}
        key = self._gain_key(voice_id)
        if key not in self._gains:
            measured = loudness(self._synth_raw(voice_id, CALIBRATION, 1.0))
            if measured is None:
                return 0.0
            self._gains[key] = round(float(np.clip(TARGET_LUFS - measured, -12.0, 20.0)), 2)
            try:
                self._gain_file().write_text(json.dumps(self._gains, indent=1), encoding="utf-8")
            except OSError:
                pass
        return self._gains[key]

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
        """Speak one piece of text with one voice at audiobook pace and loudness. Returns trimmed
        audio (may be empty). speed 1.0 = natural narration; 1.1 is about the old 1.0."""
        voice_id = self.resolve(voice_id)
        with self.gpu:
            audio = self._synth_raw(voice_id, text, speed * NATURAL_PACE)
            if audio.size:
                audio = limit(audio * np.float32(10 ** (self.voice_gain(voice_id) / 20)))
        return audio

    def _synth_raw(self, voice_id: str, text: str, speed: float) -> np.ndarray:
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
                    parts.append(silence(RUN_GAP / max(speed, 0.5)))
                parts.append(a)
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)
