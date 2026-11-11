"""Package the built app as an update and publish it as a GitHub release.

    python tools/publish.py            build must be done first (publish.cmd does both)
    python tools/publish.py --dry-run  only make the files in releases/<version>/

The release gets:  AiBookReader-app-<version>.zip  (exe + _internal/assets + _internal/runtime.json)
                   update.json                     (version, runtime, asset, sha256, size)
Release notes: releases/<version>/notes.md when it exists, otherwise the commit subjects since the
last release tag.
"""
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import __version__  # noqa: E402
from app.updater import EXE_NAME, REPO  # noqa: E402

DIST = ROOT / "dist" / "Ai Book Reader"


def sh(*args, check=True) -> str:
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def notes_for(version: str, out: Path) -> Path:
    custom = out / "notes.md"
    if custom.exists():
        return custom
    last = sh("git", "describe", "--tags", "--abbrev=0", check=False)
    rng = f"{last}..HEAD" if last else "HEAD"
    subjects = [s for s in sh("git", "log", rng, "--format=%s").splitlines() if s.strip()]
    body = f"## Ai Book Reader {version}\n\n" + "\n".join(f"- {s}" for s in subjects)
    body += ("\n\n---\nUpdate from inside the app (**Update** button). This release contains the app update only; "
             "the AI runtime (PyTorch, voices) stays as installed.")
    custom.write_text(body, encoding="utf-8")
    return custom


def main() -> None:
    dry = "--dry-run" in sys.argv
    version = __version__
    exe = DIST / EXE_NAME
    runtime_file = DIST / "_internal" / "runtime.json"
    if not exe.exists() or not runtime_file.exists():
        raise SystemExit("Build first (build.cmd): dist is missing the exe or runtime.json")
    out = ROOT / "releases" / version
    out.mkdir(parents=True, exist_ok=True)
    asset = f"AiBookReader-app-{version}.zip"
    zpath = out / asset
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(exe, EXE_NAME)
        z.write(runtime_file, "_internal/runtime.json")
        for p in sorted((DIST / "_internal" / "assets").rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(DIST).as_posix())
    sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
    manifest = {"version": version, "runtime": json.loads(runtime_file.read_text())["runtime"],
                "asset": asset, "sha256": sha, "size": zpath.stat().st_size}
    (out / "update.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    notes = notes_for(version, out)
    print(f"{asset}: {zpath.stat().st_size / 1e6:.1f} MB, sha256 {sha[:16]}..., runtime {manifest['runtime']}")
    if dry:
        print("dry run - nothing published")
        return
    if sh("git", "status", "--porcelain"):
        raise SystemExit("Commit your changes first.")
    sh("git", "push", "origin", "HEAD")
    tag = f"v{version}"
    if tag in sh("gh", "release", "list", "--repo", REPO, "--limit", "100", check=False):
        raise SystemExit(f"Release {tag} already exists - bump __version__ in app/__init__.py")
    sh("gh", "release", "create", tag, str(zpath), str(out / "update.json"), "--repo", REPO,
       "--title", f"Ai Book Reader {version}", "--notes-file", str(notes), "--target", sh("git", "rev-parse", "HEAD"))
    print(f"Published {tag}: https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
