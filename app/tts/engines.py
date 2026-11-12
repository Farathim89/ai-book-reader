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


KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_V11_REPO = "hexgrad/Kokoro-82M-v1.1-zh"      # its English voices: Maple, Sol, Vale


def _byte_bar(bars: list, tick):
    """A tqdm class for hf_hub_download(tqdm_class=...): counts bytes quietly and calls tick() on each update.
    Works for both download paths (the fast parallel Xet one and plain HTTP)."""
    import io

    from tqdm import tqdm

    class Bar(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs.pop("name", None)
            kwargs["disable"] = False
            kwargs.setdefault("file", io.StringIO())        # no console in the app
            super().__init__(*args, **kwargs)
            bars.append(self)

        def update(self, n=1):
            out = super().update(n)
            tick()
            return out
    return Bar


def download_with_progress(repo: str, files: list[str], label: str, notify) -> None:
    """Fetch files of a Hugging Face model repo that are not cached yet, telling notify(text) how far it is.
    Uses the fast (parallel Xet) download when available - about 50x quicker here than plain HTTP."""
    from huggingface_hub import HfApi, hf_hub_download, try_to_load_from_cache
    missing = [f for f in files if not isinstance(try_to_load_from_cache(repo, f), str)]
    if not missing:
        return
    sizes = {s.rfilename: s.size or 0 for s in HfApi().model_info(repo, files_metadata=True).siblings
             if s.rfilename in missing}
    total = sum(sizes.values()) or 1
    size = f"{total / 1e9:.1f} GB" if total >= 1e9 else f"{total / 1e6:.0f} MB"
    state = {"done": 0, "shown": 0}
    notify(f"{label}: 0% of {size}")
    for f in missing:
        bars: list = []

        def tick(f=f, bars=bars):
            part = min(max((b.n for b in bars), default=0), sizes.get(f, 0))     # Xet keeps two bars: same bytes
            pct = min(99, int(100 * (state["done"] + part) / total))
            if pct > state["shown"]:
                state["shown"] = pct
                notify(f"{label}: {pct}% of {size}")
        hf_hub_download(repo, f, tqdm_class=_byte_bar(bars, tick))
        state["done"] += sizes.get(f, 0)


class KokoroEngine:
    """hexgrad/Kokoro-82M - fast, high quality. Voices: its ready-made ones, three more from the Kokoro 1.1
    model (loaded the first time one is used), and voicepack files (.pt) made by kokoro_search.py."""
    name = "kokoro"
    max_chars = 350

    def __init__(self):
        self.model = None
        self.model11 = None
        self.pipes: dict[str, object] = {}
        self.pipes11: dict[str, object] = {}
        self._load_lock = threading.Lock()
        self.notify = lambda text: None

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
            self.model = offline_first(lambda: KModel(repo_id=KOKORO_REPO).to(_device()).eval())
            self._pipe("a")
            self.synth("Ready.", "af_heart", 1.0)   # warm-up

    def _pipe(self, lang: str):
        if lang not in self.pipes:
            from kokoro import KPipeline
            self.pipes[lang] = KPipeline(lang_code=lang, repo_id=KOKORO_REPO, model=self.model)
        return self.pipes[lang]

    def _pipe11(self, lang: str):
        """Pipeline on the Kokoro 1.1 model (downloads ~330 MB the first time)."""
        with self._load_lock:
            if self.model11 is None:
                from kokoro import KModel
                download_with_progress(KOKORO_V11_REPO, ["config.json", "kokoro-v1_1-zh.pth"],
                                       "Downloading the Kokoro 1.1 voices (once)", self.notify)
                self.notify("Loading the Kokoro 1.1 voices…")
                self.model11 = offline_first(lambda: KModel(repo_id=KOKORO_V11_REPO).to(_device()).eval())
                self.notify("")
        if lang not in self.pipes11:
            from kokoro import KPipeline
            self.pipes11[lang] = KPipeline(lang_code=lang, repo_id=KOKORO_V11_REPO, model=self.model11)
        return self.pipes11[lang]

    def forget_voice(self, voice: str) -> None:
        """Drop a cached voicepack (a .pt file that was made again or removed)."""
        for pipe in list(self.pipes.values()) + list(self.pipes11.values()):
            pipe.voices.pop(voice, None)

    def synth(self, text: str, voice: str, speed: float, lang: str | None = None) -> np.ndarray:
        """voice: a Kokoro voice name or a voicepack file; lang: "a" American / "b" British pronunciation
        (by default from the voice name; voicepack files: American)."""
        import torch

        from .voices import KOKORO_V11_VOICES
        lang = lang or ("b" if voice.startswith(("bf_", "bm_")) else "a")
        pipe = self._pipe11(lang) if voice in KOKORO_V11_VOICES else self._pipe(lang)
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
        download_with_progress(FULL_REPO, FULL_FILES, "Downloading the high-quality voice model (once)", self.notify)

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
