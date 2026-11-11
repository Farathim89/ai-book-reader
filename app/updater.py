"""One-click updates from GitHub releases (like ABS-DA).

A release carries:
  AiBookReader-Portable-<v>.zip  the whole small portable app (first download)
  AiBookReader-src-<v>.zip       only the app: app/, assets/, runtime/, AiBookReader.pyw and the
                                 launcher "Ai Book Reader.exe" (~1 MB) - what the Update button installs
  update.json                    {"version", "runtime": "lite", "asset", "sha256", "size",
                                  "lite": {"asset", "sha256", "size", "requirements"}}
The AI parts (PyTorch, voices...) live in the portable's own Python. When a new version needs other
versions of them, the Set up window installs just those after the restart.

Older builds (one big PyInstaller exe) read the top-level fields; their runtime differs from "lite",
so they are told to download the new portable once instead of half-updating.

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
LITE_DIRS = ("app", "assets", "runtime")          # replaced as a whole by a lite update


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
    needs_setup: bool = False       # lite: the new version installs other AI parts after the restart
    full_download: bool = False     # no package this copy can install: download the portable again

    @property
    def runtime_ok(self) -> bool:
        if self.full_download:
            return False
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


def is_lite() -> bool:
    from .setup_runtime import is_lite as lite
    return lite()


def can_install() -> bool:
    """Can this copy update itself (portable app), or is it run from source?"""
    return is_frozen() or is_lite()


def local_runtime() -> str:
    try:
        return json.loads((resource_dir() / "runtime.json").read_text(encoding="utf-8")).get("runtime", "")
    except (OSError, ValueError):
        return ""


def check(timeout: float = 10, lite: bool | None = None) -> Release | None:
    """The newest release when it is newer than this app, else None."""
    lite = is_lite() if lite is None else lite
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
    runtime = m.get("runtime", "")
    needs_setup = full = False
    part = m
    if lite:
        if m.get("lite"):
            part, runtime = m["lite"], ""
            from .setup_runtime import installed
            needs_setup = bool(part.get("requirements")) and part["requirements"] != installed().get("requirements")
        else:                                         # a release without a small-portable package
            full = True
    z = assets.get(part.get("asset", ""))
    if not z:
        raise UpdateError(f"Release {tag} is missing {part.get('asset') or 'its app package'}")
    sha = part.get("sha256", "")
    digest = z.get("digest") or ""            # GitHub's own fingerprint, when it publishes one
    if digest.startswith("sha256:"):
        if sha and digest[7:] != sha:
            raise UpdateError("The update's fingerprints do not match. Not installing it.")
        sha = digest[7:]
    return Release(version=tag.lstrip("vV"), tag=tag, notes=rel.get("body") or "",
                   page=rel.get("html_url") or RELEASES_PAGE, zip_url=z["browser_download_url"], zip_name=z["name"],
                   size=int(z.get("size") or 0), sha256=sha, runtime=runtime, needs_setup=needs_setup,
                   full_download=full)


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


# ---- automatic updates: downloaded in the background, installed when the app closes ------------
def _pending_file() -> Path:
    return sub("cache", "update") / "pending.json"


def save_pending(rel: Release, zip_path: Path) -> None:
    _pending_file().write_text(json.dumps({"version": rel.version, "zip": str(zip_path), "sha256": rel.sha256}),
                               encoding="utf-8")


def clear_pending() -> None:
    _pending_file().unlink(missing_ok=True)


def pending() -> dict | None:
    """The downloaded update waiting to be installed (newer than this app), or None."""
    try:
        p = json.loads(_pending_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if parse_version(p.get("version", "0")) <= parse_version(current_version()) or not Path(p.get("zip", "")).exists():
        clear_pending()
        return None
    return p


def install_pending() -> Path | None:
    """Install the waiting update (its fingerprint is checked again). Returns the program to start, or None."""
    p = pending()
    if not p or not can_install():
        return None
    z = Path(p["zip"])
    try:
        if p.get("sha256") and hashlib.sha256(z.read_bytes()).hexdigest() != p["sha256"]:
            z.unlink(missing_ok=True)
            return None
        return install(z)
    finally:
        clear_pending()


def _safe_names(z: zipfile.ZipFile) -> list[str]:
    names = []
    for name in z.namelist():
        parts = Path(name).parts
        if name.endswith("/") or ".." in parts or name.startswith(("/", "\\")) or (parts and ":" in parts[0]):
            continue
        names.append(name)
    return names


def _replace_exe(dest: Path) -> None:
    """A running exe cannot be overwritten, but it can be renamed (deleted on the next start)."""
    if dest.exists() and dest.suffix.lower() == ".exe":
        old = dest.with_name(dest.name + ".old")
        old.unlink(missing_ok=True)
        dest.rename(old)


def install(zip_path: Path, target: Path | None = None) -> Path:
    """Put the new files in place and return the program to restart."""
    target = Path(target or app_dir())
    with zipfile.ZipFile(zip_path) as z:
        names = _safe_names(z)
        if EXE_NAME not in names:
            raise UpdateError("The update package has no program file.")
        if "app/main.py" in names:
            _install_lite(z, names, target)
            return target / EXE_NAME
        for name in names:                      # PyInstaller build: exe + a few files in _internal
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            _replace_exe(dest)
            with z.open(name) as src, open(dest, "wb") as f:
                shutil.copyfileobj(src, f)
    return target / EXE_NAME


def _install_lite(z: zipfile.ZipFile, names: list[str], target: Path) -> None:
    """Small portable: unpack next to the app, then swap the app/assets/runtime folders as a whole
    (so files removed in the new version are really gone); the old ones are deleted on the next start."""
    stage = target / ".update-new"
    shutil.rmtree(stage, ignore_errors=True)
    for name in names:
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with z.open(name) as src, open(dest, "wb") as f:
            shutil.copyfileobj(src, f)
    for item in sorted(stage.iterdir()):
        dest = target / item.name
        if item.is_dir():
            old = target / f"{item.name}.old"
            shutil.rmtree(old, ignore_errors=True)
            try:
                if dest.exists():
                    dest.rename(old)
                item.rename(dest)
            except OSError:                     # a file is locked (virus scanner...): copy over instead
                if not dest.exists() and old.exists():
                    old.rename(dest)
                shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            _replace_exe(dest)
            shutil.copy2(item, dest)
    shutil.rmtree(stage, ignore_errors=True)


def restart(exe: Path) -> None:
    subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def cleanup_old() -> None:
    """Remove what the previous update left behind."""
    if not can_install():
        return
    root = app_dir()
    for p in root.glob("*.exe.old"):
        try:
            p.unlink()
        except OSError:
            pass
    for name in LITE_DIRS:
        shutil.rmtree(root / f"{name}.old", ignore_errors=True)
    shutil.rmtree(root / ".update-new", ignore_errors=True)
