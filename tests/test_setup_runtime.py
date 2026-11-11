import json
import sys

import pytest

from app import setup_runtime as S


@pytest.fixture
def lite(monkeypatch, tmp_path):
    """Pretend to be the small portable: runtime/ files and the install marker in tmp_path."""
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "requirements.txt").write_text("kokoro>=0.9.4\n# comment\nnumpy\n", encoding="utf-8")
    (rt / "constraints.txt").write_text("numpy==2.2.6\nkokoro==0.9.4\n", encoding="utf-8")
    monkeypatch.setattr(S, "runtime_dir", lambda: rt)
    monkeypatch.setattr(S, "marker", lambda: tmp_path / "runtime-installed.json")
    monkeypatch.setattr(S, "is_lite", lambda: True)
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    return rt


def test_requirements_hash_follows_the_files(lite):
    h = S.requirements_hash()
    (lite / "requirements.txt").write_text("numpy\nkokoro>=0.9.4\n", encoding="utf-8")     # same set, other order
    assert S.requirements_hash() == h
    (lite / "constraints.txt").write_text("numpy==2.2.7\nkokoro==0.9.4\n", encoding="utf-8")
    assert S.requirements_hash() != h


def test_setup_needed(lite, monkeypatch):
    assert S.setup_needed() == "install"
    S.mark_done("cuda")
    assert S.setup_needed() is None
    assert json.loads(S.marker().read_text())["device"] == "cuda"
    (lite / "constraints.txt").write_text("numpy==3.0.0\n", encoding="utf-8")
    assert S.setup_needed() == "update"
    monkeypatch.setattr(S, "is_lite", lambda: False)
    assert S.setup_needed() is None          # dev venv / old exe: never


def test_steps_use_pins_wheels_and_the_right_torch(lite):
    (lite / "wheels").mkdir()
    steps = S.build_steps("cuda", models=True)
    assert len(steps) == 5
    torch = steps[0][1]
    assert f"torch=={S.TORCH_VERSION}+cu128" in torch and "https://download.pytorch.org/whl/cu128" in torch
    reqs = steps[1][1]
    assert "-c" in reqs and "--find-links" in reqs and ":all:" in reqs
    assert all("--no-deps" in s[1] for s in steps[2:4])
    cpu = S.build_steps("cpu", models=False)
    assert len(cpu) == 4 and f"torch=={S.TORCH_VERSION}+cpu" in cpu[0][1]


def test_run_steps_reports_progress_and_failures(lite, monkeypatch):
    monkeypatch.setattr(S, "python_dir", lambda: __import__("pathlib").Path(sys.executable).parent)
    ok_script = ("print('Collecting kokoro==0.9.4'); print('Downloading kokoro-0.9.4-py3-none-any.whl (5 MB)'); "
                 "print('Progress 2500000 of 5000000'); print('Installing collected packages: kokoro')")
    logs, states, progress = [], [], []
    ok, err = S.run_steps([("Good", ["-c", ok_script]), ("Bad", ["-c", "import sys; sys.exit(3)"]),
                           ("Never", ["-c", "pass"])],
                          logs.append, lambda i, s: states.append((i, s)), lambda f, d: progress.append((f, d)),
                          lambda: False)
    assert not ok and "exit code 3" in err and "Bad" in err
    assert states == [(0, "running"), (0, "done"), (1, "running"), (1, "failed")]
    assert (0.5, "kokoro-0.9.4-py3-none-any.whl   2 of 5 MB") in progress
    assert (1.0, "Installing…") in progress
    assert any("Collecting kokoro" in ln for ln in logs) and not any(ln.startswith("Progress") for ln in logs)
