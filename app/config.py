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
    "export_abs_layout": True,
    # breaks like a human narrator's (measured on real audiobooks: most sentence ends 0.4-0.9 s,
    # paragraphs ~1 s); the voices' own trailing silence comes on top
    "pause_sentence": 0.45,
    "pause_paragraph": 1.0,
    "pause_heading": 1.2,
    "pause_chapter": 2.0,
    "ollama_url": "http://127.0.0.1:11434",
    "text_model": "qwen2.5:14b",
    "vision_model": "qwen2.5vl:7b",
    "library_sort": "recent",
    "sort_ignore_articles": False,  # True: "The Hobbit" sorts under H (like Audiobookshelf's prefix option)
    "notify_tasks": True,
    "check_updates": True,
    "auto_update": False,           # download updates in the background, install them when the app closes
    "window_geometry": None,
    "window_state": None,
    "settings_version": 2,
}

# defaults of earlier versions: a stored value that still equals one was never changed by the user
OLD_DEFAULTS = {1: {"pause_sentence": 0.18, "pause_paragraph": 0.5, "pause_heading": 0.8, "pause_chapter": 1.5}}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self.values: dict[str, Any] = dict(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = None
        if isinstance(stored, dict):
            self.values.update(stored)
            self._migrate(int(stored.get("settings_version") or 1))

    def _migrate(self, version: int) -> None:
        """Move untouched old defaults to the new ones (e.g. the 1.2.2 human-like pauses)."""
        for v in range(version, DEFAULTS["settings_version"]):
            for key, old in OLD_DEFAULTS.get(v, {}).items():
                if self.values.get(key) == old:
                    self.values[key] = DEFAULTS[key]
        self.values["settings_version"] = DEFAULTS["settings_version"]

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
