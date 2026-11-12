"""Find the best part of a long recording (an audiobook, a podcast) to clone a voice from.

Windows spread over the whole recording are scored on:
  * typical voice  - how close it sounds to the recording's main voice (Chatterbox's small speaker
                     encoder; the average over all windows is the narrator), so character voices,
                     other speakers and music score low
  * clean          - speech level against the quiet between words (background music / noise raise it)
  * flowing speech - mostly speech with natural pauses, no long silences
The first 10 seconds count most: that is what Chatterbox listens to."""
from __future__ import annotations

import re
import subprocess

import numpy as np

from .voices import ffmpeg_exe, load_audio_any

SR = 16000
_VE = None


def media_duration(path: str) -> float | None:
    exe = ffmpeg_exe()
    if not exe:
        return None
    r = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def speaker_encoder():
    """Chatterbox's voice encoder (5 MB, from the models Set up downloads), on the CPU; None if missing."""
    global _VE
    if _VE is not None:
        return _VE or None
    try:
        from huggingface_hub import try_to_load_from_cache
        path = next((p for p in (try_to_load_from_cache(repo, "ve.safetensors") for repo in
                                 ("ResembleAI/chatterbox-turbo", "ResembleAI/chatterbox")) if isinstance(p, str)), None)
        if path is None:
            _VE = False
            return None
        from chatterbox.models.voice_encoder import VoiceEncoder
        from safetensors.torch import load_file
        ve = VoiceEncoder()
        ve.load_state_dict(load_file(path))
        _VE = ve.eval()
    except Exception:  # noqa: BLE001 - without it the finder still scores clean speech
        _VE = False
    return _VE or None


def speech_scores(a: np.ndarray) -> tuple[float, float, float]:
    """(clean 0..1, flowing 0..1, speech level dB) for 16 kHz mono audio."""
    hop = SR // 50
    n = len(a) // hop
    if n < 50:
        return 0.0, 0.0, -100.0
    db = 20 * np.log10(np.sqrt(np.mean(a[: n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1)) + 1e-9)
    level, floor = np.percentile(db, 90), np.percentile(db, 10)
    active = db > level - 25
    clean = float(np.clip((level - floor - 18) / 27, 0, 1))
    activity = float(active.mean())
    flowing = float(np.clip(1 - abs(activity - 0.82) / 0.5, 0, 1))
    gap = run = 0
    for on in active:                                   # longest pause
        run = 0 if on else run + 1
        gap = max(gap, run)
    if gap * 0.02 > 1.2:
        flowing *= max(0.0, 1 - (gap * 0.02 - 1.2) / 2)
    if level < -45:                                     # nearly silent window
        clean = flowing = 0.0
    return clean, flowing, float(level)


def find_best_clip(path: str, seconds: float = 20, progress=None, max_windows: int = 48) -> dict:
    """{"start": seconds, "score", "typical", "clean", "flowing", "windows"} - the best place to start
    a `seconds`-long clip. progress(fraction, message) is called as it goes."""
    report = progress or (lambda f, m: None)
    total = media_duration(path) or 0.0
    if total <= seconds * 1.5:
        return {"start": 0.0, "score": 1.0, "typical": 1.0, "clean": 1.0, "flowing": 1.0, "windows": 1}
    skip = min(60.0, total * 0.05)                      # opening credits, music
    count = int(np.clip((total - skip - seconds) // seconds, 1, max_windows))
    starts = np.linspace(skip, total - seconds - 1, count)
    windows = []
    for k, s in enumerate(starts):
        report(0.85 * k / count, f"Listening through the recording… {k + 1} of {count}")
        a = load_audio_any(path, SR, float(s), seconds)
        if len(a) < SR * min(seconds, 8):
            continue
        head = a[: SR * 10]
        c1, f1, lvl = speech_scores(head)
        c2, f2, _ = speech_scores(a)
        windows.append({"start": float(s), "audio": head, "clean": 0.7 * c1 + 0.3 * c2,
                        "flowing": 0.7 * f1 + 0.3 * f2, "level": lvl})
    if not windows:
        return {"start": 0.0, "score": 0.0, "typical": 0.0, "clean": 0.0, "flowing": 0.0, "windows": 0}
    report(0.88, "Comparing the voices…")
    ve = speaker_encoder()
    if ve is not None and len(windows) >= 3:
        emb = np.asarray(ve.embeds_from_wavs([w["audio"] for w in windows], sample_rate=SR))
        emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        centre = emb.mean(axis=0)
        sims = emb @ (centre / np.linalg.norm(centre))
        keep = sims >= np.median(sims)                  # the narrator: centre of the typical half
        centre = emb[keep].mean(axis=0)
        sims = emb @ (centre / np.linalg.norm(centre))
        lo, hi = float(sims.min()), float(sims.max())
        for w, sim in zip(windows, sims):
            w["typical"] = float(sim)
            w["typical_n"] = (float(sim) - lo) / (hi - lo) if hi > lo else 1.0
    else:
        for w in windows:
            w["typical"], w["typical_n"] = 0.0, 0.5
    for w in windows:
        w["score"] = 0.5 * w["typical_n"] + 0.3 * w["clean"] + 0.2 * w["flowing"]
    best = max(windows, key=lambda w: w["score"])
    report(1.0, "Found it")
    return {"start": round(best["start"], 1), "score": round(best["score"], 3), "typical": round(best["typical"], 3),
            "clean": round(best["clean"], 3), "flowing": round(best["flowing"], 3), "windows": len(windows)}
