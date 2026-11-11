"""Background jobs and the ABS-style tasks panel.

GPU-heavy jobs (audiobook exports, page reading, character detection) wait in a queue and run
one at a time; light jobs (library scans, downloads) start right away. Finished jobs stay in
the history so they can be retried or their file shown."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme
from .widgets import IconButton, muted


class Job(QObject):
    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)
    ended = Signal()          # always emitted last (success, failure or cancel)

    def __init__(self, title: str, fn: Callable, cancellable: bool = True, on_cancel: Callable | None = None,
                 heavy: bool = False):
        super().__init__()
        self.title = title
        self.fn = fn
        self.cancellable = cancellable
        self.on_cancel = on_cancel
        self.heavy = heavy
        self._cancel = threading.Event()
        self.result = None
        self.error: str | None = None
        self.state = "new"                    # new | queued | running | done | failed | cancelled
        self.frac = 0.0
        self.msg = ""
        self.started_at = 0.0
        self.ended_at = 0.0
        self.progress.connect(self._track)

    def _track(self, f: float, m: str) -> None:
        self.frac = f
        if m:
            self.msg = m

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
        self.state = "running"
        self.started_at = time.time()
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


class JobManager(QObject):
    """Runs jobs; heavy ones one after another. `jobs` = running + queued (for busy checks)."""
    changed = Signal()
    finished = Signal(object)        # a job ended (done / failed / cancelled)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history: list[Job] = []
        self._handlers: dict[int, tuple] = {}

    @property
    def jobs(self) -> list[Job]:
        return [j for j in self.history if j.state in ("queued", "running")]

    def running(self) -> list[Job]:
        return [j for j in self.history if j.state == "running"]

    def queued(self) -> list[Job]:
        return [j for j in self.history if j.state == "queued"]

    def run(self, job: Job, on_done=None, on_failed=None, on_ended=None) -> Job:
        """Start (or queue) a job. Handlers passed here are kept for Retry."""
        self.history.append(job)
        self._handlers[id(job)] = (on_done, on_failed, on_ended)
        if on_done:
            job.done.connect(on_done)
        if on_failed:
            job.failed.connect(on_failed)
        if on_ended:
            job.ended.connect(on_ended, Qt.QueuedConnection)
        job.ended.connect(lambda j=job: self._ended(j), Qt.QueuedConnection)
        job.progress.connect(lambda *_: self.changed.emit())
        if job.heavy and any(j.heavy for j in self.running()):
            job.state = "queued"
        else:
            job.start()
        self._trim()
        self.changed.emit()
        return job

    def _ended(self, job: Job) -> None:
        if job.state in ("cancelled",):
            pass
        elif job.cancelled:
            job.state = "cancelled"
        elif job.error:
            job.state = "failed"
        else:
            job.state = "done"
            job.frac = 1.0
        job.ended_at = time.time()
        if job.heavy:
            nxt = next((j for j in self.history if j.state == "queued" and j.heavy), None)
            if nxt is not None:
                nxt.start()
        self.finished.emit(job)
        self.changed.emit()

    def _trim(self) -> None:
        finished = [j for j in self.history if j.state in ("done", "failed", "cancelled")]
        for j in finished[:-40]:
            self.history.remove(j)

    def busy(self, title: str) -> bool:
        return any(j.title == title for j in self.jobs)

    def busy_prefix(self, prefix: str) -> bool:
        return any(j.title.startswith(prefix) for j in self.jobs)

    def stop(self, job: Job) -> None:
        if job.state == "queued":
            job.cancel()
            job.state = "cancelled"
            job.ended_at = time.time()
            self.changed.emit()
        elif job.state == "running":
            job.cancel()
            job.msg = "Stopping…"
            self.changed.emit()

    def retry(self, job: Job) -> Job:
        new = Job(job.title, job.fn, job.cancellable, job.on_cancel, job.heavy)
        handlers = self._handlers.pop(id(job), (None, None, None))
        if job in self.history:
            self.history.remove(job)
        return self.run(new, *handlers)

    def clear_finished(self) -> None:
        self.history = [j for j in self.history if j.state in ("queued", "running")]
        self.changed.emit()

    def cancel_all(self) -> None:
        for j in list(self.jobs):
            j.cancel()


# ------------------------------------------------------------------------------------------ UI
def _ago(t: float) -> str:
    s = int(time.time() - t)
    return "just now" if s < 60 else f"{s // 60} min ago" if s < 3600 else f"{s // 3600} h ago"


def _took(j: Job) -> str:
    s = int(j.ended_at - j.started_at) if j.started_at else 0
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


class JobsButton(QToolButton):
    """App-bar tasks button with a badge for running + queued jobs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText(theme.I["list"])
        theme.use_icon_font(self, 19)
        self.setToolTip("Tasks")
        self.setCursor(Qt.PointingHandCursor)
        self.count = 0
        self.active = False

    def set_count(self, n: int, active: bool) -> None:
        self.count, self.active = n, active
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if not self.count:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = 17
        r = QRectF(self.width() - d - 1, 1, d, d)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.YELLOW))
        p.drawEllipse(r)
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#1a1a1a"))
        p.drawText(r, Qt.AlignCenter, str(self.count))


class JobRow(QFrame):
    def __init__(self, job: Job, manager: JobManager, on_show=None, parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("JobRow")
        self.setStyleSheet(f"#JobRow {{ background: {theme.SURFACE}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        icon = {"running": "▶", "queued": "⋯", "done": "✓", "failed": "✗",
                "cancelled": "■"}.get(job.state, "")
        color = {"running": theme.YELLOW, "done": theme.GREEN_HOVER, "failed": theme.DANGER}.get(job.state, theme.MUTED)
        state = QLabel(icon)
        state.setStyleSheet(f"color: {color}; font-size: 11pt;")
        state.setFixedWidth(18)
        top.addWidget(state)
        title = QLabel(job.title)
        title.setStyleSheet("font-weight: 600;")
        top.addWidget(title, 1)
        if job.state in ("running", "queued") and job.cancellable:
            b = QPushButton("Stop")
            b.clicked.connect(lambda: manager.stop(job))
            top.addWidget(b)
        if job.state in ("failed", "cancelled"):
            b = QPushButton("Retry")
            b.clicked.connect(lambda: manager.retry(job))
            top.addWidget(b)
        path = job.result if isinstance(job.result, str) else (
            job.result[0][0] if isinstance(job.result, tuple) and job.result and job.result[0] else None)
        if job.state == "done" and on_show and path and isinstance(path, str):
            b = QPushButton("Show file")
            b.clicked.connect(lambda: on_show(path))
            top.addWidget(b)
        lay.addLayout(top)
        if job.state == "running":
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(max(0.0, min(1.0, job.frac)) * 1000))
            bar.setFixedHeight(6)
            lay.addWidget(bar)
            detail = job.msg or "Working…"
        elif job.state == "queued":
            detail = "Waiting for the GPU – starts when the job before it is done"
        elif job.state == "done":
            detail = f"Done in {_took(job)} · {_ago(job.ended_at)}" + (f" · {job.msg}" if job.msg and job.msg != "Done" else "")
        elif job.state == "failed":
            detail = f"Failed: {job.error}"
        else:
            detail = f"Stopped · {_ago(job.ended_at)}"
        d = muted(detail)
        d.setWordWrap(True)
        lay.addWidget(d)


class JobsPanel(QFrame):
    """Drop-down list of tasks under the app-bar button."""

    def __init__(self, manager: JobManager, on_show=None, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.on_show = on_show
        self.setObjectName("JobsPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        head = QHBoxLayout()
        t = QLabel("Tasks")
        t.setStyleSheet("font-size: 12pt; font-weight: 600;")
        head.addWidget(t)
        head.addStretch(1)
        self.clear = QPushButton("Clear finished")
        self.clear.setObjectName("Link")
        self.clear.clicked.connect(manager.clear_finished)
        head.addWidget(self.clear)
        close = IconButton("close", "Close", 11)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        lay.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        manager.changed.connect(lambda: self.isVisible() and not self._timer.isActive() and self._timer.start())
        self.restyle()
        self.hide()

    def restyle(self) -> None:
        self.setStyleSheet(f"#JobsPanel {{ background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; "
                           "border-radius: 8px; }")

    def refresh(self) -> None:
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(6)
        order = {"running": 0, "queued": 1, "failed": 2, "cancelled": 3, "done": 4}
        jobs = sorted(self.manager.history, key=lambda j: (order.get(j.state, 5), -(j.ended_at or j.started_at)))
        if not jobs:
            v.addWidget(muted("No tasks yet. Exports, page reading and character detection show up here."))
        for j in jobs:
            v.addWidget(JobRow(j, self.manager, self.on_show))
        v.addStretch(1)
        self.scroll.setWidget(inner)
        self.clear.setVisible(any(j.state in ("done", "failed", "cancelled") for j in jobs))

    def toggle_at(self, pos: QPoint) -> None:
        if self.isVisible():
            self.hide()
            return
        self.setFixedSize(480, 460)
        self.move(pos)
        self.refresh()
        self.show()
        self.raise_()
