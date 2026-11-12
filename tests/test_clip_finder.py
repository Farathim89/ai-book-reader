import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

from app.tts import clip_finder as CF  # noqa: E402
from app.tts import voices as V  # noqa: E402


def _speechlike(seconds: float, sr: int, rng, noise_db: float | None = None) -> np.ndarray:
    """Bursts of 'syllables' with short pauses; optional steady background noise."""
    out = []
    while sum(len(x) for x in out) < seconds * sr:
        n = int(rng.uniform(0.15, 0.4) * sr)
        t = np.arange(n) / sr
        envelope = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.02)          # 20 ms in and out, like syllables
        burst = 0.3 * np.sin(2 * np.pi * rng.uniform(120, 250) * t) * envelope
        out += [burst, np.zeros(int(rng.uniform(0.04, 0.12) * sr))]
    a = np.concatenate(out)[: int(seconds * sr)]
    if noise_db is not None:
        a = a + rng.normal(0, 10 ** (noise_db / 20), a.size)
    return a.astype(np.float32)


def test_speech_scores_tell_clean_speech_from_noise_and_silence():
    rng = np.random.default_rng(1)
    clean, flowing, _ = CF.speech_scores(_speechlike(10, 16000, rng))
    noisy_clean, _, _ = CF.speech_scores(_speechlike(10, 16000, rng, noise_db=-22))
    silent = CF.speech_scores(np.zeros(16000 * 10, np.float32))
    assert clean > 0.8 and flowing > 0.5
    assert noisy_clean < clean - 0.3
    assert silent[:2] == (0.0, 0.0)


def test_find_best_clip_skips_silence_and_noisy_parts(tmp_path, monkeypatch):
    if not V.ffmpeg_exe():
        pytest.skip("needs ffmpeg")
    monkeypatch.setattr(CF, "speaker_encoder", lambda: None)
    rng = np.random.default_rng(2)
    sr = 16000
    audio = np.concatenate([np.zeros(sr * 90, np.float32),                # intro silence
                            _speechlike(120, sr, rng, noise_db=-20),        # speech over music / noise
                            _speechlike(120, sr, rng)])                     # clean narration from 210 s
    p = tmp_path / "book.wav"
    sf.write(str(p), audio, sr)
    steps = []
    r = CF.find_best_clip(str(p), 20, progress=lambda f, m: steps.append(f))
    assert r["start"] >= 205 and r["clean"] > 0.8
    assert steps and steps[-1] == 1.0
    short = tmp_path / "short.wav"
    sf.write(str(short), audio[: sr * 25], sr)
    assert CF.find_best_clip(str(short), 20)["start"] == 0.0           # nothing to choose from
