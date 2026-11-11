"""Build the small portable app (~100 MB) and its release files.

    python tools/build_lite.py                    build + zips in releases/<version>/
    python tools/build_lite.py --install DIR      also put the new app into an existing portable folder
                                                  (keeps its data\\ and the AI parts in its python\\)
    python tools/build_lite.py --no-zip           only build build/lite/Ai Book Reader/

The portable holds:
    Ai Book Reader.exe      tiny launcher (tools/launcher.cs, built with the C# compiler of Windows)
    AiBookReader.pyw        entry script
    app/ assets/            the app
    runtime/                requirements + exact versions (constraints) + wheels PyPI lacks
    python/                 Python 3.10 (embeddable) with PySide6 and requests pre-installed
On first start the Set up window (app/setup_runtime.py) installs PyTorch, the voices and OCR into
python/. Release files (releases/<version>/):
    AiBookReader-Portable-<v>.zip   the whole portable (first download)
    AiBookReader-src-<v>.zip        app only (~1 MB): what the in-app Update installs
    update.json, notes.md
Runs the same on GitHub Actions (.github/workflows/release.yml) and locally."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import APP_NAME, __version__  # noqa: E402
from app.setup_runtime import requirements_hash  # noqa: E402
from app.updater import EXE_NAME, REPO  # noqa: E402

PY_VERSION = "3.10.11"
PY_EMBED = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PY_EMBED_SHA256 = "608619f8619075629c9c69f361352a0da6ed7e62f83a0e19c63e0ea32eb7629d"
PRE_INSTALLED = ["pip", "PySide6-Essentials", "requests"]      # versions from runtime/constraints.txt
SDIST_ONLY = ["docopt", "wordninja"]          # PyPI has no wheels for these: built here, shipped in runtime/wheels
RUNTIME = ROOT / "runtime"
BUILD = ROOT / "build" / "lite"
STAGE = BUILD / APP_NAME
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
SRC_PARTS = [EXE_NAME, "AiBookReader.pyw", "app", "assets", "runtime"]       # what an update replaces
# Qt parts the app never loads (it uses QtCore/QtGui/QtWidgets + the SVG image plugin): developer
# tools, QML/Quick, Designer, translations of Qt's own texts, the software OpenGL fallback, type stubs
PYSIDE_TRIM = ["*.exe", "*.pyi", "opengl32sw.dll", "qml", "translations", "metatypes", "typesystems", "include",
               "glue", "doc", "resources", "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6Designer*.dll", "Qt6LabsStyleKit*.dll",
               "Qt6UiTools.dll", "Qt6Help.dll", "Qt6Test.dll", "Qt6Sql.dll", "QtQuick*.pyd", "QtQml*.pyd",
               "QtDesigner.pyd", "QtLabsStyleKit.pyd", "QtUiTools.pyd", "QtHelp.pyd", "QtTest.pyd", "QtOpenGL*.pyd",
               "QtSql.pyd", "plugins/designer", "plugins/qmltooling", "plugins/sqldrivers"]


def log(msg: str) -> None:
    print(msg, flush=True)


def run(*args, **kw) -> None:
    log("  > " + " ".join(str(a) for a in args))
    subprocess.run([str(a) for a in args], check=True, **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path, expect: str = "") -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        log(f"  downloading {url}")
        part = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": "AiBookReader-build"})
        with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        part.replace(dest)
    got = sha256(dest)
    if expect and got != expect:
        dest.unlink()
        raise SystemExit(f"{dest.name}: SHA-256 {got} does not match the pinned {expect}")
    return dest


def pinned(name: str) -> str:
    """name==version from runtime/constraints.txt."""
    key = re.sub(r"[-_.]+", "-", name).lower()
    for ln in (RUNTIME / "constraints.txt").read_text(encoding="utf-8").splitlines():
        if ln.split("==")[0].strip().lower() == key:
            return f"{name}=={ln.split('==')[1].strip()}"
    raise SystemExit(f"{name} is not in runtime/constraints.txt (run tools/freeze_runtime.py)")


# ------------------------------------------------------------------------------------------ parts
def build_python(fresh: bool) -> Path:
    """python/: the embeddable Python plus the pre-installed packages (cached in build/lite/python)."""
    out = BUILD / "python"
    stamp_file = BUILD / "python.stamp"
    stamp = hashlib.sha256(json.dumps([PY_VERSION, PRE_INSTALLED, PYSIDE_TRIM,
                                       [pinned(n) for n in PRE_INSTALLED]]).encode()).hexdigest()
    if not fresh and out.is_dir() and stamp_file.exists() and stamp_file.read_text() == stamp:
        log("python: cached")
        return out
    log(f"python: Python {PY_VERSION} embeddable + {', '.join(PRE_INSTALLED)}")
    if out.exists():
        shutil.rmtree(out)
    embed = fetch(PY_EMBED, BUILD / "cache" / Path(PY_EMBED).name, PY_EMBED_SHA256)
    with zipfile.ZipFile(embed) as z:
        z.extractall(out)
    # ._pth: the zipped standard library, site-packages, and "import site" (for .pth files).
    # A ._pth file also keeps this Python away from any other Python on the PC (no PYTHONPATH, no user site).
    pth = next(out.glob("python3*._pth"))
    pth.write_text(f"{pth.stem}.zip\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    site = out / "Lib" / "site-packages"
    site.mkdir(parents=True)
    run(sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
        "--only-binary", ":all:", "--target", site, "-c", RUNTIME / "constraints.txt", *PRE_INSTALLED)
    shutil.rmtree(site / "bin", ignore_errors=True)
    trim_pyside(site / "PySide6")
    stamp_file.write_text(stamp)
    return out


def trim_pyside(folder: Path) -> None:
    """Drop the Qt developer tools and modules the app never loads (QML/Quick, Designer...)."""
    before = folder_size(folder)
    for pattern in PYSIDE_TRIM:
        for p in folder.glob(pattern):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    log(f"  PySide6: {before / 1e6:.0f} MB -> {folder_size(folder) / 1e6:.0f} MB")


def folder_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def build_wheels(dest: Path) -> None:
    """Wheels of the packages PyPI only has as source; the Set up window installs binaries only."""
    dest.mkdir(parents=True, exist_ok=True)
    run(sys.executable, "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps", "-w", dest,
        *[pinned(n) for n in SDIST_ONLY])


def build_launcher(dest: Path) -> None:
    if not CSC.exists():
        raise SystemExit(f"C# compiler not found: {CSC} (it comes with Windows / .NET Framework 4)")
    parts = (__version__.split(".") + ["0", "0", "0"])[:3]
    src = BUILD / "launcher.cs"
    src.write_text((ROOT / "tools" / "launcher.cs").read_text(encoding="utf-8").replace("@VERSION@", ".".join(parts)),
                   encoding="utf-8")
    run(CSC, "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
        f"/win32icon:{ROOT / 'assets' / 'icon.ico'}", "/reference:System.Windows.Forms.dll", f"/out:{dest}", src)


def copy_tree(src: Path, dest: Path) -> None:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def stage(fresh: bool) -> None:
    log(f"Building {APP_NAME} {__version__} (small portable) in {STAGE}")
    py = build_python(fresh)
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    log("python: copying")
    shutil.copytree(py, STAGE / "python")
    log("app: copying")
    copy_tree(ROOT / "app", STAGE / "app")
    copy_tree(ROOT / "assets", STAGE / "assets")
    (STAGE / "runtime").mkdir()
    for name in ("requirements.txt", "constraints.txt"):
        shutil.copy2(RUNTIME / name, STAGE / "runtime" / name)
    shutil.copy2(ROOT / "AiBookReader.pyw", STAGE / "AiBookReader.pyw")
    log("wheels:")
    build_wheels(STAGE / "runtime" / "wheels")
    log("launcher:")
    build_launcher(STAGE / EXE_NAME)


# ------------------------------------------------------------------------------------------ checks
def check_resolution() -> None:
    """Can pip install every AI part from binaries only (as the Set up window does)? Resolves
    against PyPI without installing anything; PyTorch resolves to its PyPI build here."""
    log("check: all AI parts resolve from wheels")
    with tempfile.TemporaryDirectory() as tmp:
        run(sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--disable-pip-version-check",
            "--ignore-installed", "--only-binary", ":all:", "--target", tmp, "-r", RUNTIME / "requirements.txt",
            "-c", RUNTIME / "constraints.txt", "--find-links", STAGE / "runtime" / "wheels")


def smoke_test() -> None:
    log("check: the portable's Python starts and sees itself as the small portable")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import PySide6.QtWidgets, requests; "
            "from app.setup_runtime import is_lite, setup_needed, build_steps; "
            "assert is_lite(), 'not lite'; print('setup needed:', setup_needed()); "
            "assert len(build_steps('cpu', True)) == 6")
    run(STAGE / "python" / "python.exe", "-c", code, STAGE, cwd=STAGE)


# ------------------------------------------------------------------------------------------ release files
def notes(version: str, out: Path) -> Path:
    path = out / "notes.md"
    if path.exists():
        return path
    tag = f"v{version}"

    def git(*a) -> str:
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip() if r.returncode == 0 else ""
    body = git("tag", "-l", "--format=%(contents)", tag)            # annotated tag message
    body = re.sub(r"-----BEGIN PGP SIGNATURE-----.*", "", body, flags=re.S).strip()
    if not body:
        head = tag if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}") else "HEAD"
        last = git("describe", "--tags", "--abbrev=0", f"{head}^")
        subjects = git("log", f"{last}..{head}" if last else head, "--format=%s").splitlines()
        body = "\n".join(f"- {s}" for s in subjects if s.strip()) or "- Maintenance"
    body = f"## {APP_NAME} {version}\n\n{body}\n\n---\n" + (
        "**New here?** Download `AiBookReader-Portable-" + version + ".zip`, unpack it anywhere and start "
        "`Ai Book Reader.exe`. The first start installs the AI parts (PyTorch, voices, OCR; 6-9 GB, about 15 GB free space needed) into the "
        "folder.\n\n**Already using it?** Click **Update** in the app; it downloads only the app (about 1 MB).")
    path.write_text(body + "\n", encoding="utf-8")
    return path


def zip_dir(zpath: Path, items: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, arc in items:
            if src.is_dir():
                for p in sorted(src.rglob("*")):
                    if p.is_file() and "__pycache__" not in p.parts:
                        z.write(p, f"{arc}/{p.relative_to(src).as_posix()}")
            else:
                z.write(src, arc)


def package(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    v = __version__
    portable = out / f"AiBookReader-Portable-{v}.zip"
    src = out / f"AiBookReader-src-{v}.zip"
    log(f"zip: {portable.name}")
    zip_dir(portable, [(STAGE, APP_NAME)])
    log(f"zip: {src.name}")
    zip_dir(src, [(STAGE / p, p) for p in SRC_PARTS])
    manifest = {
        "version": v,
        "runtime": "lite",          # older one-exe builds compare this with theirs: not equal = full download
        "asset": portable.name, "sha256": sha256(portable), "size": portable.stat().st_size,
        "lite": {"asset": src.name, "sha256": sha256(src), "size": src.stat().st_size,
                 "requirements": requirements_hash()},
    }
    (out / "update.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    notes(v, out)
    for p in (portable, src):
        log(f"  {p.name}: {p.stat().st_size / 1e6:.1f} MB")


# ------------------------------------------------------------------------------------------ local install
def running_from(folder: Path) -> bool:
    ps = ("Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '" +
          str(folder / "python").replace("'", "''") + "\\*' } | Measure-Object | ForEach-Object Count")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return r.stdout.strip() not in ("", "0")


def install_into(folder: Path) -> None:
    """Update a portable folder in place: app files replaced, data\\ and python\\ (AI parts) kept."""
    folder = folder.resolve()
    if running_from(folder):
        raise SystemExit(f"{APP_NAME} is running from {folder}: close it first.")
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "python" / "python.exe").exists():
        log(f"install: new portable in {folder}")
        shutil.copytree(STAGE / "python", folder / "python", dirs_exist_ok=True)
    for part in SRC_PARTS:
        src, dest = STAGE / part, folder / part
        if src.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    log(f"install: {APP_NAME} {__version__} -> {folder}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", metavar="DIR", help="update this portable folder in place")
    ap.add_argument("--no-zip", action="store_true", help="skip the release zips")
    ap.add_argument("--fresh", action="store_true", help="rebuild python\\ instead of using the cached one")
    ap.add_argument("--skip-checks", action="store_true")
    ap.add_argument("--tag", help="fail unless this git tag matches the app version (CI)")
    a = ap.parse_args()
    if a.tag and a.tag.lstrip("v") != __version__:
        raise SystemExit(f"Tag {a.tag} does not match the app version {__version__} (app/__init__.py)")
    stage(a.fresh)
    if not a.skip_checks:
        check_resolution()
        smoke_test()
    if not a.no_zip:
        package(ROOT / "releases" / __version__)
    if a.install:
        install_into(Path(a.install))
    log("done")


if __name__ == "__main__":
    main()
