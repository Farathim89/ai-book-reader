"""First-start installer of the small portable app.

The portable download only carries Python, the Qt UI and the app itself (~100 MB). The AI parts
(PyTorch, the voice engines, OCR, spaCy - about 4 GB) are installed by this window into the
portable's own Python the first time it starts, and again (only what changed) when an update
needs other versions. Everything stays inside the portable folder.

Versions come from runtime/constraints.txt (the tested dev venv), so every user gets the same set."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from .paths import app_dir, data_dir, resource_dir

RUNTIME_REV = 1                       # bump to make every portable run the Set up window again
TORCH_VERSION = "2.11.0"
TORCH_INDEX = {"cuda": ("cu128", "https://download.pytorch.org/whl/cu128"),
               "cpu": ("cpu", "https://download.pytorch.org/whl/cpu")}
SPACY_MODEL = ("https://github.com/explosion/spacy-models/releases/download/"
               "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl")
CHATTERBOX = "chatterbox-tts==0.1.7"
NEED_GB = 15         # free disk space to ask for (PyTorch ~5 GB + other parts ~1.5 GB + models ~4.5 GB + download cache)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------------------------------ checks
def python_dir() -> Path:
    return Path(sys.executable).resolve().parent


def is_lite() -> bool:
    """Running from the small portable's own Python (python\\ next to the app)?"""
    if getattr(sys, "frozen", False):
        return False
    embedded = app_dir() / "python"
    return embedded.is_dir() and python_dir() == embedded.resolve()


def runtime_dir() -> Path:
    return resource_dir() / "runtime"


def requirements_hash() -> str:
    """Fingerprint of everything the Set up window installs; a change means 'run Set up again'."""
    h = hashlib.sha256(f"{RUNTIME_REV}|{TORCH_VERSION}|{CHATTERBOX}|{SPACY_MODEL}".encode())
    for name in ("requirements.txt", "constraints.txt"):
        try:
            text = (runtime_dir() / name).read_text(encoding="utf-8")
        except OSError:
            continue
        lines = sorted(ln.split("#")[0].strip() for ln in text.splitlines())
        h.update("\n".join(ln for ln in lines if ln).encode())
    return h.hexdigest()[:16]


def marker() -> Path:
    return python_dir() / "runtime-installed.json"


def installed() -> dict:
    try:
        return json.loads(marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def setup_needed() -> str | None:
    """'install' (first start), 'update' (this version needs other AI parts) or None."""
    if not is_lite():
        return None
    done = installed()
    if not done:
        return "install"
    return None if done.get("requirements") == requirements_hash() else "update"


def nvidia_gpu() -> str | None:
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        r = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                           timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    return lines[0] if r.returncode == 0 and lines else None


def free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return 999.0


# ------------------------------------------------------------------------------------------ steps
def build_steps(device: str, models: bool) -> list[tuple[str, list[str]]]:
    pip = ["-m", "pip", "install", "--no-warn-script-location", "--progress-bar", "raw", "--disable-pip-version-check"]
    pins = ["-c", str(runtime_dir() / "constraints.txt")] if (runtime_dir() / "constraints.txt").exists() else []
    wheels = runtime_dir() / "wheels"           # pre-built wheels of the few packages PyPI only has as source
    links = ["--find-links", str(wheels)] if wheels.is_dir() else []
    tag, index = TORCH_INDEX[device]
    steps = [
        (f"PyTorch {TORCH_VERSION} ({'CUDA 12.8 for your NVIDIA card' if device == 'cuda' else 'CPU only'})",
         pip + [f"torch=={TORCH_VERSION}+{tag}", f"torchaudio=={TORCH_VERSION}+{tag}", "--index-url", index]),
        ("Voices, book formats and OCR",
         pip + ["-r", str(runtime_dir() / "requirements.txt"), *pins, *links, "--only-binary", ":all:"]),
        ("Voice cloning (Chatterbox)", pip + [CHATTERBOX, "--no-deps"]),
        ("English language model", pip + [SPACY_MODEL, "--no-deps"]),
    ]
    if models:
        steps.append(("Voice models (Kokoro + Chatterbox, about 4.5 GB)", ["-c", MODELS_SCRIPT]))
    return steps


# Downloads exactly the files the app loads later, file by file with a byte count for the progress bar.
MODELS_SCRIPT = r"""
import os
from fnmatch import fnmatch
from huggingface_hub import HfApi, hf_hub_download
tok = os.getenv("HF_TOKEN") or None
jobs = [("hexgrad/Kokoro-82M", ["config.json", "kokoro-v1_0.pth", "voices/*.pt"]),
        ("ResembleAI/chatterbox-turbo", ["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"])]
api = HfApi()
files = []
for repo, pats in jobs:
    for s in api.model_info(repo, files_metadata=True, token=tok).siblings:
        if any(fnmatch(s.rfilename, p) for p in pats):
            files.append((repo, s.rfilename, s.size or 0))
total = sum(f[2] for f in files) or 1
done = 0
for repo, name, size in files:
    print(f"Downloading {repo.split('/')[-1]}/{name}", flush=True)
    hf_hub_download(repo, name, token=tok)
    done += size
    print(f"Progress {done} of {total}", flush=True)
"""


def step_env() -> dict:
    env = dict(os.environ)
    # the same model folder the app uses (paths.setup_model_env keeps a HF_HOME the user set)
    env.update({"HF_HOME": os.environ.get("HF_HOME") or str(data_dir() / "models" / "huggingface"),
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_PROGRESS_BARS": "1",
                "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                # pip's download cache stays in the portable and is removed when Set up is done
                "PIP_CACHE_DIR": str(data_dir() / "cache" / "pip")})
    for key in ("PYTHONPATH", "PYTHONHOME", "PIP_REQUIRE_VIRTUALENV", "PIP_USER", "PIP_TARGET", "PIP_PREFIX"):
        env.pop(key, None)
    return env


def run_steps(steps, on_log, on_step, on_progress, cancelled, set_proc=None) -> tuple[bool, str]:
    """Run the Set up steps one after another with the portable's python.exe."""
    py = str(python_dir() / "python.exe")
    env = step_env()
    for i, (name, args) in enumerate(steps):
        if cancelled():
            return False, "Cancelled"
        on_step(i, "running")
        on_log(f"\n=== {name} ===")
        try:
            # -u / -X utf8: unbuffered UTF-8 output (the portable's Python ignores PYTHON* variables)
            proc = subprocess.Popen([py, "-u", "-X", "utf8", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", env=env, cwd=str(app_dir()),
                                    creationflags=NO_WINDOW)
        except OSError as e:
            on_step(i, "failed")
            return False, str(e)
        if set_proc:
            set_proc(proc)
        current = ""
        for line in proc.stdout:
            line = line.rstrip()
            m = re.match(r"\s*Progress (\d+) of (\d+)", line)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                if total:
                    detail = f"{current}   {done / 1e6:,.0f} of {total / 1e6:,.0f} MB" if total > 2e6 else current
                    on_progress(done / total, detail)
                continue
            d = re.match(r"\s*(Downloading|Collecting|Installing collected packages)\b:?\s*(\S+)?", line)
            if d:
                if d.group(1).startswith("Installing"):
                    on_progress(1.0, "Installing\u2026")
                else:
                    current = unquote(d.group(2) or "").rstrip("/").split("/")[-1][:70]
                    on_progress(-1, current)
            on_log(line)
        rc = proc.wait()
        if cancelled():
            return False, "Cancelled"
        if rc != 0:
            on_step(i, "failed")
            return False, f"\u201c{name}\u201d did not finish (exit code {rc}). The details show why."
        on_step(i, "done")
    return True, ""


def mark_done(device: str) -> None:
    marker().write_text(json.dumps({"requirements": requirements_hash(), "device": device,
                                    "date": time.strftime("%Y-%m-%d %H:%M")}, indent=1), encoding="utf-8")
    shutil.rmtree(data_dir() / "cache" / "pip", ignore_errors=True)


def launch_app(*args: str) -> None:
    launcher = app_dir() / "Ai Book Reader.exe"
    if launcher.exists() and not getattr(sys, "frozen", False):
        cmd = [str(launcher), *args]
    else:
        cmd = [str(python_dir() / "pythonw.exe"), str(app_dir() / "AiBookReader.pyw"), *args]
    subprocess.Popen(cmd, cwd=str(app_dir()), creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


# ------------------------------------------------------------------------------------------ window
INTRO = {
    "install": ("Set up {app}",
                "The AI parts are downloaded once and stay inside this portable folder: PyTorch, the voice "
                "engines (Kokoro and Chatterbox), the book readers and OCR. Nothing is installed elsewhere on "
                "your PC, and you can move the folder later."),
    "update": ("Update the AI parts",
               "This version of {app} needs new or newer AI parts. Only what changed is downloaded; your library, "
               "settings and voices stay as they are."),
    "repair": ("Repair the AI parts",
               "Installs the AI parts again (only missing or broken ones are downloaded). Your library, settings "
               "and voices stay as they are."),
}


def run_setup_window(mode: str) -> int:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                                   QProgressBar, QPushButton, QVBoxLayout, QWidget)

    from . import APP_NAME, __version__
    from .ui import theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    theme.apply_theme(app)

    class Bus(QObject):
        log = Signal(str)
        step = Signal(int, str)            # index, running / done / failed
        progress = Signal(float, str)
        finished = Signal(bool, str)

    class SetupWindow(QWidget):
        def __init__(self):
            super().__init__()
            title, intro = INTRO.get(mode, INTRO["install"])
            self.setWindowTitle(f"{APP_NAME} {__version__} \u2013 {title.format(app=APP_NAME)}")
            icon = resource_dir() / "assets" / "icon.png"
            if icon.exists():
                self.setWindowIcon(QIcon(str(icon)))
            self.resize(740, 600)
            self.gpu = nvidia_gpu()
            self.device = "cuda" if self.gpu else "cpu"
            self.bus = Bus()
            self.cancel = threading.Event()
            self.proc: subprocess.Popen | None = None
            self.ok = False
            self.running = False
            lay = QVBoxLayout(self)
            lay.setContentsMargins(28, 24, 28, 22)
            lay.setSpacing(10)
            head = QLabel(title.format(app=APP_NAME))
            head.setStyleSheet("font-size: 19pt; font-weight: 600;")
            lay.addWidget(head)
            t = QLabel(intro.format(app=APP_NAME))
            t.setWordWrap(True)
            t.setObjectName("Muted")
            lay.addWidget(t)
            gpu = QLabel(f"\u2713  {self.gpu}: voices run on the graphics card (PyTorch with CUDA, about 2.8 GB)."
                         if self.gpu else
                         "No NVIDIA graphics card found, so the CPU version of PyTorch is used (about 200 MB). "
                         "Voices work, only slower.")
            gpu.setWordWrap(True)
            gpu.setStyleSheet(f"color: {theme.GREEN_HOVER if self.gpu else theme.YELLOW};")
            lay.addWidget(gpu)
            self.models = QCheckBox("Also download the voice models now (about 4.5 GB), so the first Play starts at once")
            self.models.setChecked(mode != "update")
            self.models.setVisible(mode != "update")
            lay.addWidget(self.models)
            free = free_gb(app_dir())
            if free < NEED_GB:
                warn = QLabel(f"Only {free:.1f} GB are free on this drive; about {NEED_GB} GB are needed.")
                warn.setStyleSheet(f"color: {theme.DANGER};")
                lay.addWidget(warn)
            lay.addSpacing(6)
            self.step_box = QVBoxLayout()
            self.step_box.setSpacing(6)
            lay.addLayout(self.step_box)
            self.step_labels: list[QLabel] = []
            self._make_steps()
            self.models.toggled.connect(lambda _: self._make_steps())
            lay.addSpacing(6)
            self.bar = QProgressBar()
            self.bar.setRange(0, 1000)
            self.bar.setTextVisible(False)
            self.bar.setFixedHeight(8)
            lay.addWidget(self.bar)
            self.status = QLabel("About 6 to 9 GB are downloaded (10-30 minutes on a fast line)." if mode == "install"
                                 else "Ready.")
            self.status.setObjectName("Muted")
            lay.addWidget(self.status)
            self.details_btn = QPushButton("Show details")
            self.details_btn.setObjectName("Link")
            self.details_btn.setCursor(Qt.PointingHandCursor)
            self.details_btn.clicked.connect(self._toggle_details)
            lay.addWidget(self.details_btn, 0, Qt.AlignLeft)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(5000)
            self.log.setStyleSheet("font-family: Consolas; font-size: 9pt;")
            self.log.hide()
            lay.addWidget(self.log, 1)
            lay.addStretch(1)
            row = QHBoxLayout()
            self.quit_btn = QPushButton("Quit")
            self.quit_btn.clicked.connect(self.close)
            self.go = QPushButton({"install": "Install", "update": "Update", "repair": "Repair"}.get(mode, "Install"))
            self.go.setObjectName("Accent")
            self.go.setDefault(True)
            self.go.clicked.connect(self._start)
            row.addStretch(1)
            row.addWidget(self.quit_btn)
            row.addWidget(self.go)
            lay.addLayout(row)
            b = self.bus
            b.log.connect(self.log.appendPlainText)
            b.step.connect(self._on_step)
            b.progress.connect(self._on_progress)
            b.finished.connect(self._on_finished)

        def _make_steps(self) -> None:
            while self.step_box.count():
                w = self.step_box.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self.steps = build_steps(self.device, self.models.isChecked())
            self.step_labels = []
            for name, _ in self.steps:
                lab = QLabel(f"\u25cb    {name}")
                lab.setStyleSheet(f"color: {theme.MUTED};")
                self.step_box.addWidget(lab)
                self.step_labels.append(lab)

        def _toggle_details(self) -> None:
            self.log.setVisible(not self.log.isVisible())
            self.details_btn.setText("Hide details" if self.log.isVisible() else "Show details")

        def _start(self) -> None:
            if self.ok:
                launch_app()
                self.close()
                return
            self.go.setEnabled(False)
            self.models.setEnabled(False)
            self.status.setStyleSheet("")
            self.cancel.clear()
            self._make_steps()
            self.running = True
            threading.Thread(target=self._work, daemon=True).start()

        def _work(self) -> None:
            b = self.bus
            ok, err = run_steps(self.steps, b.log.emit, b.step.emit, b.progress.emit, self.cancel.is_set,
                                set_proc=lambda p: setattr(self, "proc", p))
            if ok:
                try:
                    mark_done(self.device)
                except OSError as e:
                    ok, err = False, f"Could not save the Set up result: {e}"
            if not self.cancel.is_set():
                b.finished.emit(ok, err)

        def _on_step(self, i: int, state: str) -> None:
            name = self.steps[i][0]
            icon, color = {"running": ("\u25b6", theme.YELLOW), "done": ("\u2713", theme.GREEN_HOVER),
                           "failed": ("\u2717", theme.DANGER)}[state]
            self.step_labels[i].setText(f"{icon}    {name}")
            self.step_labels[i].setStyleSheet(f"color: {color};" + (" font-weight: 600;" if state == "running" else ""))
            if state == "running":
                self.bar.setValue(0)
                self.status.setText(f"Step {i + 1} of {len(self.steps)}: {name}")

        def _on_progress(self, f: float, detail: str) -> None:
            if f >= 0:
                self.bar.setValue(int(max(0.0, min(1.0, f)) * 1000))
            if detail:
                self.status.setText(detail)

        def _on_finished(self, ok: bool, err: str) -> None:
            self.running = False
            self.ok = ok
            self.go.setEnabled(True)
            if ok:
                self.bar.setValue(1000)
                self.status.setText("All set. The AI parts are installed.")
                self.go.setText(f"Start {APP_NAME}")
                self.quit_btn.setText("Close")
            else:
                self.status.setText(err)
                self.status.setStyleSheet(f"color: {theme.DANGER};")
                self.go.setText("Try again")
                self.models.setEnabled(True)
                if not self.log.isVisible():
                    self._toggle_details()

        def closeEvent(self, e) -> None:
            if self.running and QMessageBox.question(
                    self, APP_NAME, "Stop the Set up? It continues where it stopped next time.") != QMessageBox.Yes:
                e.ignore()
                return
            self.cancel.set()
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
            super().closeEvent(e)

    w = SetupWindow()
    w.show()
    from .winshell import set_window_identity
    set_window_identity(int(w.winId()))
    return app.exec()
