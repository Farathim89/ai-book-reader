"""Install the AI parts for real into the just-built portable, then run its self-test.

Used by GitHub Actions after tools/build_lite.py (the release zips are already made, so the
installed parts never end up in them). On a PC without an NVIDIA card it installs the CPU
version of PyTorch, exactly like a user's first start would.

    python tools/test_setup.py [--no-models]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "build" / "lite" / "Ai Book Reader"

SETUP = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from app import setup_runtime as S
assert S.is_lite(), "the portable's Python does not see itself as the portable"
assert S.setup_needed() == "install", S.setup_needed()
device = "cuda" if S.nvidia_gpu() else "cpu"
steps = S.build_steps(device, models=sys.argv[2] == "1")
t0 = time.time()
def step(i, state):
    print(f"[{state}] {steps[i][0]}  ({time.time() - t0:.0f}s)", flush=True)
ok, err = S.run_steps(steps, lambda line: print("    " + line, flush=True), step, lambda f, d: None, lambda: False)
if not ok:
    sys.exit("Set up failed: " + err)
S.mark_done(device)
assert S.setup_needed() is None
print(f"Set up done in {time.time() - t0:.0f}s ({device})", flush=True)
"""


def main() -> int:
    models = "--no-models" not in sys.argv
    py = STAGE / "python" / "python.exe"
    if not py.exists():
        sys.exit("Build first: python tools/build_lite.py")
    data = Path(tempfile.mkdtemp(prefix="abr_setup_test_"))
    env = {k: v for k, v in os.environ.items() if k not in ("HF_HOME", "PYTHONPATH", "PYTHONHOME")}
    env["ABR_DATA_DIR"] = str(data)
    print(f"== Set up in {STAGE} (data: {data})", flush=True)
    subprocess.run([str(py), "-u", "-c", SETUP, str(STAGE), "1" if models else "0"], env=env, cwd=STAGE, check=True)
    if not models:
        return 0
    print("== Self-test", flush=True)
    return subprocess.run([str(py), "-u", str(STAGE / "AiBookReader.pyw"), "--selftest"], env=env, cwd=STAGE).returncode


if __name__ == "__main__":
    sys.exit(main())
