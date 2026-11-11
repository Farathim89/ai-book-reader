"""Background jobs (export, character analysis, OCR, library scan, model downloads)
with a compact progress pill in the status bar."""
from __future__ import annotations

import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QWidget

from .widgets import IconButton


class Job(QObject):
    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)
    ended = Signal()          # always emitted last (success, failure or cancel)

    def __init__(self, title: str, fn: Callable, cancellable: bool = True, on_cancel: Callable | None = None):
        super().__init__()
        self.title = title
        self.fn = fn
        self.cancellable = cancellable
        self.on_cancel = on_cancel
        self._cancel = threading.Event()
        self.result = None
        self.error: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass

    def start(self) -> "Job":
        threading.Thread(target=self._run, name=f"job:{self.title}", daemon=True).start()
        return self

    def _run(self) -> None:
        try:
            self.result = self.fn(lambda f, m="": self.progress.emit(float(f), str(m)), self._cancel.is_set)
            if not self.cancelled:
                self.done.emit(self.result)
        except Exception as e:  # noqa: BLE001
            if not self.cancelled and e.__class__.__name__ != "Cancelled":
                self.error = str(e) or e.__class__.__name__
                traceback.print_exc()
                self.failed.emit(self.error)
        finally:
            self.ended.emit()


class JobPill(QFrame):
    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        self.setObjectName("JobPill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 4, 3)
        lay.setSpacing(8)
        self.label = QLabel(job.title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedWidth(140)
        self.bar.setFixedHeight(6)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addWidget(self.detail)
        if job.cancellable:
            b = IconButton("close", "Stop", 10)
            b.clicked.connect(job.cancel)
            lay.addWidget(b)
        job.progress.connect(self._on_progress)

    def _on_progress(self, f: float, msg: str) -> None:
        self.bar.setValue(int(max(0.0, min(1.0, f)) * 1000))
        if msg:
            self.detail.setText(msg)


class JobTray(QWidget):
    """Holds the pills of running jobs (lives in the status bar)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.jobs: list[Job] = []

    def run(self, job: Job) -> Job:
        pill = JobPill(job, self)
        self.lay.addWidget(pill)
        self.jobs.append(job)

        def finish():
            pill.deleteLater()
            if job in self.jobs:
                self.jobs.remove(job)
        job.ended.connect(finish, Qt.QueuedConnection)
        return job.start()

    def busy(self, title: str) -> bool:
        return any(j.title == title for j in self.jobs)

    def cancel_all(self) -> None:
        for j in list(self.jobs):
            j.cancel()
