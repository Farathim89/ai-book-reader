"""App settings, stored as JSON in data/settings.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import data_dir

DEFAULTS: dict[str, Any] = {
    "narrator_voice": "kokoro:af_heart",
    "speed": 1.0,
    "volume": 0.9,
    "reader_font": "Georgia",
    "font_size": 16,
    "library_folders": [],
    "export_dir": str(Path.home() / "Music" / "Audiobooks"),
    "export_bitrate": "64k",
    "pause_sentence": 0.18,
    "pause_paragraph": 0.5,
    "pause_heading": 0.8,
    "pause_chapter": 1.5,
    "ollama_url": "http://127.0.0.1:11434",
    "text_model": "qwen2.5:14b",
    "vision_model": "qwen2.5vl:7b",
    "library_sort": "recent",
    "window_geometry": None,
    "window_state": None,
}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self.values: dict[str, Any] = dict(DEFAULTS)
        try:
            self.values.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key: str) -> Any:
        return self.values.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, DEFAULTS.get(key, default))

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
        tmp.replace(self.path)
