"""Where the app keeps its data.

Everything (library DB, settings, covers, caches, cloned voices and the AI models)
lives in a ``data`` folder next to the exe (or next to the project when run from
source), so the whole app stays portable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Bundled read-only files (assets). PyInstaller unpacks them to _MEIPASS."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_dir()


def data_dir() -> Path:
    d = Path(os.environ.get("ABR_DATA_DIR") or (app_dir() / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sub(*parts: str) -> Path:
    p = data_dir().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def setup_model_env() -> None:
    """Point Hugging Face / torch caches into data/models. Call before importing them."""
    models = sub("models")
    os.environ.setdefault("HF_HOME", str(models / "huggingface"))
    os.environ.setdefault("TORCH_HOME", str(models / "torch"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # keep the GPU's compiled-kernel cache big enough that models start fast every time
    os.environ.setdefault("CUDA_CACHE_MAXSIZE", str(4 * 1024 ** 3))
