import threading
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.ui.jobs import Job, JobManager  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


def pump(app, cond, limit=5.0):
    end = time.time() + limit
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_heavy_jobs_run_one_at_a_time(qapp):
    m = JobManager()
    gate = threading.Event()
    order = []

    def slow(name):
        def run(progress, cancelled):
            order.append(f"start {name}")
            gate.wait(5)
            order.append(f"end {name}")
            return name
        return run

    a = m.run(Job("A", slow("A"), heavy=True))
    b = m.run(Job("B", slow("B"), heavy=True))
    light = m.run(Job("L", lambda p, c: "light"))
    assert a.state == "running" and b.state == "queued" and light.state == "running"
    assert m.busy("B") and m.busy_prefix("A")
    gate.set()
    assert pump(qapp, lambda: b.state == "done")
    assert order == ["start A", "end A", "start B", "end B"]
    assert a.state == "done" and a.result == "A" and not m.jobs


def test_stop_queued_and_retry_keeps_handler(qapp):
    m = JobManager()
    gate = threading.Event()
    first = m.run(Job("first", lambda p, c: gate.wait(5), heavy=True))
    queued = m.run(Job("queued", lambda p, c: "never", heavy=True))
    m.stop(queued)
    assert queued.state == "cancelled"
    gate.set()
    assert pump(qapp, lambda: first.state == "done")
    assert queued.state == "cancelled" and queued.result is None

    calls = {"n": 0}
    got = []

    def flaky(progress, cancelled):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return "ok"

    j = m.run(Job("flaky", flaky, heavy=True), on_done=got.append)
    assert pump(qapp, lambda: j.state == "failed") and j.error == "disk full"
    j2 = m.retry(j)
    assert pump(qapp, lambda: j2.state == "done")
    assert got == ["ok"] and j not in m.history
