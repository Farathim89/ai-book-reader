"""Release a new version: tag it and push. GitHub Actions (.github/workflows/release.yml) then runs
the tests, builds the small portable app and publishes the GitHub release.

    python tools/publish.py                 tag v<version> (app/__init__.py) and push
    python tools/publish.py --notes FILE    release notes from FILE (Markdown)
    python tools/publish.py --dry-run       only show what would happen

Without --notes the release notes are the commit subjects since the previous tag.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import __version__  # noqa: E402
from app.updater import REPO  # noqa: E402

NOREPLY = "users.noreply.github.com"


def sh(*args, check=True) -> str:
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notes", help="Markdown file with the release notes")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tag = f"v{__version__}"
    if sh("git", "status", "--porcelain"):
        raise SystemExit("Commit your changes first.")
    if sh("git", "tag", "-l", tag) or sh("git", "ls-remote", "--tags", "origin", tag, check=False):
        raise SystemExit(f"{tag} already exists - bump __version__ in app/__init__.py")
    last = sh("git", "describe", "--tags", "--abbrev=0", check=False)
    emails = set(sh("git", "log", f"{last}..HEAD" if last else "HEAD", "--format=%ae%n%ce").split())
    private = [e for e in emails if e and not e.endswith(NOREPLY)]
    if private:
        raise SystemExit(f"Commits with a non-noreply e-mail would be published: {', '.join(sorted(private))}")
    if a.notes:
        message = Path(a.notes).read_text(encoding="utf-8").strip()
    else:
        subjects = sh("git", "log", f"{last}..HEAD" if last else "HEAD", "--format=%s").splitlines()
        message = "\n".join(f"- {s}" for s in subjects if s.strip()) or "- Maintenance"
    print(f"Release {tag} (previous: {last or 'none'})\n\n{message}\n")
    if a.dry_run:
        print("dry run - nothing tagged or pushed")
        return
    sh("git", "tag", "-a", tag, "-m", message)
    sh("git", "push", "origin", "HEAD")
    sh("git", "push", "origin", tag)
    print(f"Pushed {tag}. GitHub Actions builds and publishes it:\n"
          f"  https://github.com/{REPO}/actions\n  https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
