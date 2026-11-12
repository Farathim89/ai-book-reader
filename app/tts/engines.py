"""The two speech engines. Both produce mono float32 audio at 24 kHz."""
from __future__ import annotations

import logging
import os
import threading
import warnings

import numpy as np

SR = 24000
log = logging.getLogger(__name__)


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def offline_first(fn):
    """Load from the local model cache without asking huggingface.co (saves ~7 s per start);
    only go online when something is not downloaded yet."""
    try:
        from huggingface_hub import constants
    except Exception:
        return fn()
    if os.environ.get("HF_HUB_OFFLINE"):
        return fn()
    constants.HF_HUB_OFFLINE = True
    try:
        return fn()
    except Exception:
        constants.HF_HUB_OFFLINE = False
        return fn()
    finally:
        constants.HF_HUB_OFFLINE = False


class KokoroEngine:
    """hexgrad/Kokoro-82M - fast, high quality, fixed voice list."""
    name = "kokoro"
    max_chars = 350

    def __init__(self):
        self.model = None
        self.pipes: dict[str, object] = {}
        self._load_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        with self._load_lock:
            if self.model is not None:
                return
            warnings.filterwarnings("ignore", category=UserWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            from kokoro import KModel
            self.model = offline_first(lambda: KModel(repo_id="hexgrad/Kokoro-82M").to(_device()).eval())
            self._pipe("a")
            self.synth("Ready.", "af_heart", 1.0)   # warm-up

    def _pipe(self, lang: str):
        if lang not in self.pipes:
            from kokoro import KPipeline
            self.pipes[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", model=self.model)
        return self.pipes[lang]

    def synth(self, text: str, voice: str, speed: float) -> np.ndarray:
        import torch
        pipe = self._pipe("b" if voice.startswith("b") else "a")
        if voice not in pipe.voices:
            offline_first(lambda: pipe.load_voice(voice))
        parts = []
        with torch.inference_mode():
            for r in pipe(text, voice=voice, speed=speed, split_pattern=None):
                if r.audio is not None:
                    parts.append(r.audio.detach().cpu().numpy().astype(np.float32))
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)


def _patch_chatterbox() -> None:
    """chatterbox-tts 0.1.7 passes float64 arrays into float32 models with new numpy/librosa."""
    import torch
    from chatterbox.models.s3tokenizer import s3tokenizer as s3
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    if getattr(ChatterboxTurboTTS, "_abr_patched", False):
        return
    orig_mel = s3.S3Tokenizer.log_mel_spectrogram

    def log_mel(self, audio, padding=0):
        if not torch.is_tensor(audio):
            audio = torch.from_numpy(np.asarray(audio))
        return orig_mel(self, audio.float(), padding)

    s3.S3Tokenizer.log_mel_spectrogram = log_mel
    orig_norm = ChatterboxTurboTTS.norm_loudness
    ChatterboxTurboTTS.norm_loudness = lambda self, wav, sr, target_lufs=-27: np.asarray(
        orig_norm(self, wav, sr, target_lufs), dtype=np.float32)
    ChatterboxTurboTTS._abr_patched = True


class ChatterboxEngine:
    """Resemble AI Chatterbox Turbo - zero-shot voice cloning from a short sample."""
    name = "chatterbox"
    max_chars = 260

    def __init__(self):
        self.model = None
        self._conds: dict[str, object] = {}
        self._load_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        with self._load_lock:
            if self.model is not None:
                return
            warnings.filterwarnings("ignore")
            logging.getLogger("chatterbox").setLevel(logging.ERROR)
            _patch_chatterbox()
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                self.model = offline_first(lambda: ChatterboxTurboTTS.from_pretrained(device=_device()))
            self._conds["default"] = self.model.conds

    def unload(self) -> None:
        with self._load_lock:
            self.model = None
            self._conds.clear()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

    def forget(self, key: str) -> None:
        self._conds.pop(key, None)

    def synth(self, text: str, key: str, ref_path: str | None, expressiveness: float = 0.5) -> np.ndarray:
        import contextlib
        import io
        import torch
        m = self.model
        if key not in self._conds:
            if not ref_path:
                key = "default"
            else:
                m.prepare_conditionals(ref_path)
                self._conds[key] = m.conds
        m.conds = self._conds[key]
        with torch.inference_mode(), contextlib.redirect_stdout(io.StringIO()):
            wav = m.generate(text)
        return wav.squeeze(0).cpu().numpy().astype(np.float32)


FULL_REPO = "ResembleAI/chatterbox"
FULL_FILES = ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]


def cfg_for(expressiveness: float) -> float:
    """More expressive = a little less 'guidance' (Resemble's advice: exaggeration up, cfg down)."""
    return max(0.3, min(0.5, 0.5 - (expressiveness - 0.5) * 0.5))


class ChatterboxFullEngine:
    """The full Chatterbox model for cloned voices: closest to the recording (same-voice score 0.97
    against 0.92 for Turbo on a real narrator), with an expressiveness control; ~1.8x real time on
    an RTX 5070 Ti. Downloaded on first use (3.2 GB), with progress through .notify(text)."""
    name = "chatterbox-full"
    max_chars = 300

    def __init__(self):
        self.model = None
        self._conds: dict[str, object] = {}
        self._load_lock = threading.Lock()
        self.notify = lambda text: None

    @property
    def loaded(self) -> bool:
        return self.model is not None

    @staticmethod
    def downloaded() -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache
        except ImportError:
            return False
        return all(isinstance(try_to_load_from_cache(FULL_REPO, f), str) for f in FULL_FILES)

    def _download(self) -> None:
        from pathlib import Path

        from huggingface_hub import HfApi, constants, hf_hub_download, try_to_load_from_cache
        sizes = {s.rfilename: s.size or 0 for s in HfApi().model_info(FULL_REPO, files_metadata=True).siblings
                 if s.rfilename in FULL_FILES}
        total = sum(sizes.values()) or 1
        blobs = Path(constants.HF_HUB_CACHE) / "models--ResembleAI--chatterbox" / "blobs"
        done = 0
        shown = 0                                     # never let the percentage go backwards
        old = constants.HF_HUB_DISABLE_XET
        constants.HF_HUB_DISABLE_XET = True          # plain download: its .incomplete file shows the progress
        try:
            for f in FULL_FILES:
                if not isinstance(try_to_load_from_cache(FULL_REPO, f), str):
                    errors: list[Exception] = []

                    def get(f=f):
                        try:
                            hf_hub_download(FULL_REPO, f)
                        except Exception as e:  # noqa: BLE001
                            errors.append(e)
                    t = threading.Thread(target=get, daemon=True)
                    t.start()
                    while t.is_alive():
                        try:
                            part = sum(p.stat().st_size for p in blobs.glob("*.incomplete")) if blobs.exists() else 0
                        except OSError:                       # a part file finished while we looked
                            part = 0
                        part = min(part, sizes.get(f, 0))     # one file at a time: never count it twice
                        shown = max(shown, min(99, int(100 * (done + part) / total)))
                        self.notify(f"Downloading the high-quality voice model (once): {shown}% of "
                                    f"{total / 1e9:.1f} GB")
                        t.join(0.5)
                    if errors:
                        raise errors[0]
                done += sizes.get(f, 0)
        finally:
            constants.HF_HUB_DISABLE_XET = old

    def load(self) -> None:
        with self._load_lock:
            if self.model is not None:
                return
            warnings.filterwarnings("ignore")
            logging.getLogger("chatterbox").setLevel(logging.ERROR)
            _patch_chatterbox()
            if not self.downloaded():
                self._download()
            self.notify("Loading the high-quality voice model…")
            from chatterbox.tts import ChatterboxTTS
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                self.model = offline_first(lambda: ChatterboxTTS.from_pretrained(device=_device()))
            self.notify("")

    def unload(self) -> None:
        with self._load_lock:
            self.model = None
            self._conds.clear()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

    def forget(self, key: str) -> None:
        for k in [k for k in self._conds if k.split("|")[0] == key]:
            self._conds.pop(k, None)
        if self.model is not None:
            from .voice_training import forget_adapter
            forget_adapter(self.model.t3, key)

    def synth(self, text: str, key: str, ref_path: str | None, expressiveness: float = 0.5,
              adapter=None) -> np.ndarray:
        """adapter: path of the voice's trained adapter (voice_training.py), switched on for this call."""
        import contextlib
        import io
        import torch
        m = self.model
        ck = f"{key}|{expressiveness:.2f}"
        if ck not in self._conds:
            if ref_path:
                m.prepare_conditionals(ref_path, exaggeration=expressiveness)
            self._conds[ck] = m.conds
        m.conds = self._conds[ck]
        from .voice_training import use_adapter
        use_adapter(m.t3, key if adapter else None, adapter)
        with torch.inference_mode(), contextlib.redirect_stdout(io.StringIO()):
            wav = m.generate(text, exaggeration=expressiveness, cfg_weight=cfg_for(expressiveness))
        return wav.squeeze(0).cpu().numpy().astype(np.float32)
