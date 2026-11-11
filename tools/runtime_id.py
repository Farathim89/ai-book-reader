"""Write <app>/_internal/runtime.json: a fingerprint of the bundled binary runtime (PyTorch, CUDA,
Qt, ...). App updates only replace the exe; when a release needs a different runtime the
updater asks for the full download instead.

Usage: python tools/runtime_id.py "dist/Ai Book Reader"
"""
import hashlib
import json
import sys
from pathlib import Path


def runtime_id(app: Path) -> str:
    internal = app / "_internal"
    h = hashlib.sha256()
    for p in sorted(internal.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".dll", ".pyd", ".so"):
            h.update(f"{p.relative_to(internal).as_posix()}|{p.stat().st_size}\n".encode())
    return h.hexdigest()[:16]


if __name__ == "__main__":
    app = Path(sys.argv[1])
    rid = runtime_id(app)
    (app / "_internal" / "runtime.json").write_text(json.dumps({"runtime": rid}), encoding="utf-8")
    print(rid)
