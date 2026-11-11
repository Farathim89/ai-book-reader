"""One-click updates from GitHub releases (like ABS-DA).

A release carries two small assets:
  update.json                 {"version", "runtime", "asset", "sha256", "size"}
  AiBookReader-app-<v>.zip    the new "Ai Book Reader.exe" (it contains all the app's Python code)
                              plus _internal/assets and _internal/runtime.json
The ~5 GB AI runtime (PyTorch, CUDA...) only changes rarely; when the release needs a different
runtime than the installed one, the app says so instead of half-updating.

Only GitHub's public API is contacted; nothing about the user is sent."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from . import __version__
from .paths import app_dir, resource_dir, sub

REPO = "Farathim89/ai-book-reader"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
EXE_NAME = "Ai Book Reader.exe"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "AiBookReader-Updater"}


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page: str
    zip_url: str
    zip_name: str
    size: int
    sha256: str
    runtime: str

    @property
    def runtime_ok(self) -> bool:
        mine = local_runtime()
        return not mine or not self.runtime or mine == self.runtime


def current_version() -> str:
    return os.environ.get("ABR_VERSION_OVERRIDE") or __version__


def parse_version(v: str) -> tuple:
    parts = []
    for p in v.strip().lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def local_runtime() -> str:
    try:
        return json.loads((resource_dir() / "runtime.json").read_text(encoding="utf-8")).get("runtime", "")
    except (OSError, ValueError):
        return ""


def check(timeout: float = 10) -> Release | None:
    """The newest release when it is newer than this app, else None."""
    try:
        r = requests.get(API_LATEST, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise UpdateError(f"Could not reach GitHub: {e}") from e
    if r.status_code == 404:
        return None            # no releases yet
    if r.status_code != 200:
        raise UpdateError(f"GitHub answered {r.status_code}")
    rel = r.json()
    tag = rel.get("tag_name", "")
    if parse_version(tag) <= parse_version(current_version()):
        return None
    assets = {a["name"]: a for a in rel.get("assets", [])}
    manifest = assets.get("update.json")
    if not manifest:
        raise UpdateError(f"Release {tag} has no update.json")
    try:
        m = requests.get(manifest["browser_download_url"], headers=HEADERS, timeout=timeout).json()
    except (requests.RequestException, ValueError) as e:
        raise UpdateError(f"Could not read the update description: {e}") from e
    z = assets.get(m.get("asset", ""))
    if not z:
        raise UpdateError(f"Release {tag} is missing {m.get('asset')}")
    sha = m.get("sha256", "")
    digest = z.get("digest") or ""            # GitHub's own fingerprint, when it publishes one
    if digest.startswith("sha256:"):
        if sha and digest[7:] != sha:
            raise UpdateError("The update's fingerprints do not match. Not installing it.")
        sha = digest[7:]
    return Release(version=tag.lstrip("vV"), tag=tag, notes=rel.get("body") or "", page=rel.get("html_url") or RELEASES_PAGE,
                   zip_url=z["browser_download_url"], zip_name=z["name"], size=int(z.get("size") or 0),
                   sha256=sha, runtime=m.get("runtime", ""))


def download(rel: Release, progress=None, cancelled=None) -> Path:
    """Download and verify the update zip. Returns its path."""
    folder = sub("cache", "update")
    out = folder / rel.zip_name
    part = out.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    try:
        with requests.get(rel.zip_url, headers=HEADERS, stream=True, timeout=(10, 60)) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or rel.size or 0)
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if cancelled and cancelled():
                        raise UpdateError("Cancelled")
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total, f"{done / 1e6:.0f} of {total / 1e6:.0f} MB")
    except requests.RequestException as e:
        raise UpdateError(f"Download failed: {e}") from e
    if rel.sha256 and h.hexdigest() != rel.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("The download does not match its published fingerprint (SHA-256). Not installing it.")
    part.replace(out)
    return out


def install(zip_path: Path, target: Path | None = None) -> Path:
    """Put the new files in place. The running exe cannot be overwritten, but it can be renamed:
    it becomes '<exe>.old' and is deleted on the next start. Returns the new exe's path."""
    target = Path(target or app_dir())
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if EXE_NAME not in names:
            raise UpdateError("The update package has no program file.")
        for name in names:
            if name.endswith("/") or ".." in Path(name).parts:
                continue
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.suffix.lower() == ".exe":
                old = dest.with_name(dest.name + ".old")
                old.unlink(missing_ok=True)
                dest.rename(old)
            with z.open(name) as src, open(dest, "wb") as f:
                shutil.copyfileobj(src, f)
    return target / EXE_NAME


def restart(exe: Path) -> None:
    subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def cleanup_old() -> None:
    """Remove the previous exe left behind by an update."""
    if not is_frozen():
        return
    for p in app_dir().glob("*.exe.old"):
        try:
            p.unlink()
        except OSError:
            pass
