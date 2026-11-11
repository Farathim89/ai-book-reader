"""ABS-style libraries: named groups of folders (EBooks, Light Novels, Manga...).

Stored in settings as {"libraries": [{"name": "EBooks", "folders": ["H:/..."]}, ...]}."""
from __future__ import annotations

import os
import re

ALL = "All libraries"
OTHER = "Other books"
CHARS_PER_SECOND = 17.0    # measured on exported audiobooks (Kokoro, speed 1.0, default pauses)


def clean_name(folder: str) -> str:
    name = os.path.basename(os.path.normpath(folder)) or folder
    return re.sub(r"^[-=_\s]+|[-=_\s]+$", "", name) or name


def libraries(settings) -> list[dict]:
    libs = settings.get("libraries")
    if not libs:   # migrate the old flat folder list
        libs = [{"name": clean_name(f), "folders": [f]} for f in settings.get("library_folders") or []]
        settings["libraries"] = libs
    return libs


def all_folders(settings) -> list[str]:
    return [f for lib in libraries(settings) for f in lib["folders"]]


def add_folder(settings, folder: str, library_name: str | None = None) -> str:
    libs = libraries(settings)
    name = library_name or clean_name(folder)
    for lib in libs:
        if lib["name"].lower() == name.lower():
            if folder not in lib["folders"]:
                lib["folders"].append(folder)
            break
    else:
        libs.append({"name": name, "folders": [folder]})
    settings["libraries"] = libs
    settings["library_folders"] = all_folders(settings)
    return name


def library_of(path: str, libs: list[dict]) -> str:
    p = os.path.normcase(os.path.abspath(path))
    for lib in libs:
        for f in lib["folders"]:
            root = os.path.normcase(os.path.abspath(f)).rstrip("\\/") + os.sep
            if p.startswith(root):
                return lib["name"]
    return OTHER


def library_names(settings, rows: list[dict]) -> list[str]:
    names = [lib["name"] for lib in libraries(settings)]
    if any(r.get("library") == OTHER for r in rows):
        names.append(OTHER)
    return names


def est_seconds(chars: int, speed: float = 1.0) -> float:
    return chars / CHARS_PER_SECOND / max(0.25, speed)


def fmt_duration(sec: float, short: bool = False) -> str:
    sec = int(max(0, sec))
    h, m = sec // 3600, sec % 3600 // 60
    if short:
        return f"{h}h {m:02d}m" if h else f"{m}m"
    if h:
        return f"{h} hr {m} min"
    return f"{max(1, m)} min" if sec >= 30 else f"{sec} sec"


def fmt_clock(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
