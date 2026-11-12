"""Voice catalogue: Kokoro's ready-made voices, Chatterbox's built-in voice and cloned voices."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..paths import sub

# (id, name, gender, accent) - best-rated voices first
KOKORO_VOICES = [
    ("af_heart", "Heart", "female", "US"), ("af_bella", "Bella", "female", "US"),
    ("af_nicole", "Nicole", "female", "US"), ("bf_emma", "Emma", "female", "UK"),
    ("af_sarah", "Sarah", "female", "US"), ("af_aoede", "Aoede", "female", "US"),
    ("af_kore", "Kore", "female", "US"), ("bf_isabella", "Isabella", "female", "UK"),
    ("af_sky", "Sky", "female", "US"), ("af_nova", "Nova", "female", "US"),
    ("bf_alice", "Alice", "female", "UK"), ("af_alloy", "Alloy", "female", "US"),
    ("af_jessica", "Jessica", "female", "US"), ("bf_lily", "Lily", "female", "UK"),
    ("af_river", "River", "female", "US"),
    ("am_michael", "Michael", "male", "US"), ("am_fenrir", "Fenrir", "male", "US"),
    ("am_puck", "Puck", "male", "US"), ("bm_george", "George", "male", "UK"),
    ("bm_fable", "Fable", "male", "UK"), ("am_adam", "Adam", "male", "US"),
    ("bm_lewis", "Lewis", "male", "UK"), ("am_echo", "Echo", "male", "US"),
    ("am_eric", "Eric", "male", "US"), ("am_liam", "Liam", "male", "US"),
    ("bm_daniel", "Daniel", "male", "UK"), ("am_onyx", "Onyx", "male", "US"),
    ("af_maple", "Maple", "female", "US"), ("af_sol", "Sol", "female", "US"), ("bf_vale", "Vale", "female", "UK"),
]
# English voices that only exist for the Kokoro 1.1 model (downloaded the first time one is used)
KOKORO_V11_VOICES = {"af_maple", "af_sol", "bf_vale"}
DEFAULT_VOICE = "kokoro:af_heart"
CHATTERBOX_DEFAULT = "chatterbox:default"


@dataclass(frozen=True)
class Voice:
    id: str          # "kokoro:af_heart", "chatterbox:default", "clone:<slug>", "fast:<slug>" (Kokoro copy of a clone)
    name: str
    gender: str      # "female" / "male" / "neutral"
    engine: str      # "kokoro" / "chatterbox"
    detail: str      # shown in lists, e.g. "US female"

    @property
    def label(self) -> str:
        return f"{self.name}  ·  {self.detail}"


def kokoro_voices() -> list[Voice]:
    return [Voice(f"kokoro:{vid}", name, g, "kokoro", f"{acc} {g}" + (", Kokoro 1.1" if vid in KOKORO_V11_VOICES else ""))
            for vid, name, g, acc in KOKORO_VOICES]


# ---- cloned voices ---------------------------------------------------------
def clones_dir() -> Path:
    return sub("voices")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "voice"


def clone_voices() -> list[Voice]:
    out = []
    for d in sorted(clones_dir().iterdir()) if clones_dir().exists() else []:
        meta = d / "voice.json"
        if d.is_dir() and meta.exists() and (d / "ref.wav").exists():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except ValueError:
                continue
            fast = m.get("model") == "turbo"
            trained = (d / "adapter.safetensors").exists() and not fast
            detail = "cloned voice, fast" if fast else "cloned voice, trained" if trained else "cloned voice"
            out.append(Voice(f"clone:{d.name}", m.get("name", d.name), m.get("gender", "neutral"),
                             "chatterbox", detail))
    return out


# How a cloned voice is made: the full Chatterbox model sounds closest to the recording (measured:
# same-voice score 0.97 vs 0.92 for Turbo) and is about a third slower; Turbo is the fast one.
CLONE_MODELS = {"full": "Best – closest to the recording", "turbo": "Fast – about a third quicker"}
DEFAULT_EXPRESSIVENESS = 0.5


def clone_settings(voice_id: str) -> dict:
    """{"model": "full" | "turbo", "expressiveness": 0.25..1.0} of a cloned voice."""
    meta = clones_dir() / voice_id.split(":", 1)[-1] / "voice.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = {}
    model = m.get("model") if m.get("model") in CLONE_MODELS else "full"
    try:
        ex = float(m.get("expressiveness", DEFAULT_EXPRESSIVENESS))
    except (TypeError, ValueError):
        ex = DEFAULT_EXPRESSIVENESS
    return {"model": model, "expressiveness": round(min(1.0, max(0.25, ex)), 2)}


def clone_adapter(voice_id: str) -> Path | None:
    """The trained adapter of a cloned voice (voice_training.py), or None when it is not trained."""
    if not voice_id.startswith("clone:"):
        return None
    p = clones_dir() / voice_id.split(":", 1)[-1] / "adapter.safetensors"
    return p if p.exists() else None


def clone_training(voice_id: str) -> dict | None:
    """What the training reported (minutes, sentences, improvement, date), or None."""
    p = clones_dir() / voice_id.split(":", 1)[-1] / "training.json"
    if clone_adapter(voice_id) is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def forget_training(voice_id: str) -> None:
    d = clones_dir() / voice_id.split(":", 1)[-1]
    for name in ("adapter.safetensors", "training.json", "dataset.json"):
        (d / name).unlink(missing_ok=True)


def set_clone_settings(voice_id: str, **changes) -> dict:
    meta = clones_dir() / voice_id.split(":", 1)[-1] / "voice.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = {}
    m.update({k: v for k, v in changes.items() if k in ("model", "expressiveness")})
    meta.write_text(json.dumps(m), encoding="utf-8")
    return clone_settings(voice_id)


def clone_ref_path(voice_id: str) -> Path | None:
    if not voice_id.startswith("clone:"):
        return None
    p = clones_dir() / voice_id.split(":", 1)[1] / "ref.wav"
    return p if p.exists() else None


def ffmpeg_exe() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _decode_with_progress(exe: str, path: str, sr: int, start: float, seconds: float, progress) -> np.ndarray:
    """Like load_audio_any, reporting 0..1 as ffmpeg works (its -progress output) through progress(f)."""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".f32")
    os.close(fd)
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path), "-t", f"{seconds:.3f}", "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", tmp]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit():      # both are microseconds
                progress(min(1.0, int(value) / 1e6 / seconds))
        err = p.stderr.read()
        if p.wait() != 0:
            raise ValueError(f"Could not read this recording ({err.strip()[-200:] or 'ffmpeg failed'}).")
        progress(1.0)
        return np.fromfile(tmp, dtype=np.float32)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_audio_any(path: str, sr: int = 24000, start: float = 0.0, seconds: float | None = None,
                   progress=None) -> np.ndarray:
    """Decode any audio file to mono float32 at ``sr`` (via ffmpeg, falls back to soundfile).
    start / seconds decode only that part: ffmpeg seeks straight there, so a 20 s clip from a
    10-hour audiobook takes a moment instead of decoding (and holding) the whole book."""
    exe = ffmpeg_exe()
    if exe and progress and seconds:
        return _decode_with_progress(exe, path, sr, start, seconds, progress)
    if exe:
        cmd = [exe, "-hide_banner", "-loglevel", "error"]
        if start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", str(path)]
        if seconds:
            cmd += ["-t", f"{seconds:.3f}"]
        cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
        r = subprocess.run(cmd, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:               # empty when start is past the end
            return np.frombuffer(r.stdout, dtype=np.float32).copy()
    import soundfile as sf
    try:
        with sf.SoundFile(str(path)) as f:
            file_sr = f.samplerate
            f.seek(min(int(start * file_sr), f.frames))
            data = f.read(int(seconds * file_sr) if seconds else -1, dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError) as e:
        raise ValueError(f"Could not read this recording ({e}).") from e
    data = data.mean(axis=1)
    if file_sr != sr:
        import librosa
        data = librosa.resample(data, orig_sr=file_sr, target_sr=sr)
    return data.astype(np.float32)


def add_clone(name: str, gender: str, source: str, start: float = 0.0, seconds: float = 20.0,
              progress=None, model: str = "full", expressiveness: float = DEFAULT_EXPRESSIVENESS) -> Voice:
    """Store a reference clip (trimmed, loudness-levelled 24 kHz mono WAV) for a cloned voice.
    progress(fraction, message) is called while it works (from this thread)."""
    report = progress or (lambda f, msg: None)
    clip = _cut_clip(source, start, seconds, report)
    slug = slugify(name)
    base, k = slug, 2
    while (clones_dir() / slug).exists():
        slug = f"{base}-{k}"
        k += 1
    d = clones_dir() / slug
    d.mkdir(parents=True)
    _write_clip(d, clip)
    (d / "voice.json").write_text(json.dumps({"name": name, "gender": gender, "source": str(source),
                                              "start": start, "seconds": seconds,
                                              "model": model if model in CLONE_MODELS else "full",
                                              "expressiveness": expressiveness}), encoding="utf-8")
    report(1.0, "Done")
    return Voice(f"clone:{slug}", name, gender, "chatterbox", "cloned voice")


def _cut_clip(source: str, start: float, seconds: float, report) -> np.ndarray:
    """The clip a cloned voice copies: `seconds` from `start`, peak-levelled."""
    report(0.0, "Reading the recording…")
    a = load_audio_any(source, 24000, start, seconds,
                       progress=lambda f: report(0.9 * f, f"Reading the recording… {int(f * seconds)} of "
                                                          f"{int(seconds)} s"))[: int(seconds * 24000)]
    report(0.92, "Saving the voice…")
    if len(a) < 6 * 24000:
        raise ValueError("The voice sample must have at least 6 seconds of speech (10-30 s works best). "
                         "Is 'Start at' past the end of the recording?")
    peak = float(np.max(np.abs(a))) or 1.0
    return (a / peak * 0.9).astype(np.float32)


def _write_clip(folder: Path, clip: np.ndarray) -> None:
    import soundfile as sf
    tmp = folder / "ref.new.wav"
    sf.write(str(tmp), clip, 24000, subtype="PCM_16")
    tmp.replace(folder / "ref.wav")                 # the old clip stays until the new one is complete


def clone_info(voice_id: str) -> dict:
    """Everything the Edit window shows: name, gender, source, start, seconds, model, expressiveness."""
    d = clones_dir() / voice_id.split(":", 1)[-1]
    try:
        m = json.loads((d / "voice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = {}
    seconds = m.get("seconds")
    if not seconds:                                  # voices made before 1.2.5: measure the clip
        try:
            import soundfile as sf
            seconds = round(sf.info(str(d / "ref.wav")).duration)
        except Exception:  # noqa: BLE001
            seconds = 20
    return {"name": m.get("name", d.name), "gender": m.get("gender", "neutral"), "source": m.get("source", ""),
            "start": float(m.get("start") or 0.0), "seconds": int(seconds), **clone_settings(voice_id)}


def update_clone(voice_id: str, name: str, gender: str, source: str, start: float, seconds: float,
                 progress=None, model: str = "full", expressiveness: float = DEFAULT_EXPRESSIVENESS,
                 recut: bool = True) -> Voice:
    """Change a cloned voice in place: same id, so books and characters that use it keep it.
    With recut, the clip is cut again from `source` (a new recording or a new start / length)."""
    report = progress or (lambda f, msg: None)
    d = clones_dir() / voice_id.split(":", 1)[-1]
    if not (d / "voice.json").exists():
        raise ValueError("This voice was deleted.")
    if recut:
        _write_clip(d, _cut_clip(source, start, seconds, report))
    m = json.loads((d / "voice.json").read_text(encoding="utf-8"))
    m.update({"name": name, "gender": gender, "model": model if model in CLONE_MODELS else "full",
              "expressiveness": expressiveness})
    if recut:
        m.update({"source": str(source), "start": start, "seconds": seconds})
    (d / "voice.json").write_text(json.dumps(m), encoding="utf-8")
    report(1.0, "Done")
    return Voice(voice_id, name, gender, "chatterbox", "cloned voice")


def remove_clone(voice_id: str) -> None:
    if voice_id.startswith("clone:"):
        shutil.rmtree(clones_dir() / voice_id.split(":", 1)[1], ignore_errors=True)


# ---- fast (Kokoro) versions of cloned voices: kokoro_search.py makes kokoro.pt in the clone's folder ----
def fast_pack(voice_id: str) -> Path | None:
    """The Kokoro voicepack of a cloned voice ("clone:x" or "fast:x"), or None when it has none."""
    if not voice_id.startswith(("clone:", "fast:")):
        return None
    p = clones_dir() / voice_id.split(":", 1)[1] / "kokoro.pt"
    return p if p.exists() else None


def fast_info(voice_id: str) -> dict | None:
    """What the search reported (likeness before / after, minutes, date), or None without a fast version."""
    p = fast_pack(voice_id)
    if p is None:
        return None
    try:
        return json.loads(p.with_name("kokoro.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remove_fast(voice_id: str) -> None:
    d = clones_dir() / voice_id.split(":", 1)[-1]
    for name in ("kokoro.pt", "kokoro.json"):
        (d / name).unlink(missing_ok=True)


ACCENTS = {"US": "American English", "UK": "British English"}


def fast_accent(voice_id: str) -> str:
    """How a fast version pronounces words: "US" (default) or "UK"."""
    info = fast_info(voice_id) or {}
    return info.get("accent") if info.get("accent") in ACCENTS else "US"


def set_fast_accent(voice_id: str, accent: str) -> None:
    p = fast_pack(voice_id)
    if p is None or accent not in ACCENTS:
        return
    info = fast_info(voice_id) or {}
    info["accent"] = accent
    p.with_name("kokoro.json").write_text(json.dumps(info, indent=1), encoding="utf-8")


def fast_voices() -> list[Voice]:
    out = []
    for v in clone_voices():
        slug = v.id.split(":", 1)[1]
        if fast_pack(v.id) is not None:
            out.append(Voice(f"fast:{slug}", f"{v.name} (fast)", v.gender, "kokoro",
                             f"{fast_accent(v.id)}, Kokoro copy of a cloned voice"))
    return out


def kokoro_arg(voice_id: str) -> str:
    """What the Kokoro engine is given: a voice name ("af_heart") or the path of a voicepack file."""
    if voice_id.startswith("fast:"):
        p = fast_pack(voice_id)
        return str(p) if p is not None else "af_heart"
    return voice_id.split(":", 1)[1]


def kokoro_lang(voice_id: str) -> str:
    """Kokoro's pronunciation for a voice: "b" (British) or "a" (American)."""
    if voice_id.startswith("fast:"):
        return "b" if fast_accent(voice_id) == "UK" else "a"
    return "b" if voice_id.split(":", 1)[-1].startswith(("bf_", "bm_")) else "a"


def all_voices() -> list[Voice]:
    return kokoro_voices() + [Voice(CHATTERBOX_DEFAULT, "Chatterbox", "female", "chatterbox",
                                    "expressive, built-in")] + clone_voices() + fast_voices()


def find_voice(voice_id: str | None) -> Voice:
    for v in all_voices():
        if v.id == voice_id:
            return v
    return kokoro_voices()[0]
