import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows shell")

from app import winshell  # noqa: E402


def test_shortcut_carries_the_app_id(tmp_path):
    lnk = tmp_path / "Ai Book Reader.lnk"
    winshell.make_shortcut(lnk, Path(sys.executable))
    assert lnk.exists() and lnk.stat().st_size > 0
    assert winshell.read_app_id(lnk) == winshell.APP_ID


def test_window_gets_relaunch_command(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    if not isinstance(app, QApplication) or QApplication.platformName() != "windows":
        pytest.skip("needs a QApplication with real windows (not offscreen / QCoreApplication)")
    w = QWidget()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    exe = tmp_path / "Ai Book Reader.exe"
    assert winshell.set_window_identity(int(w.winId()), exe)
    assert winshell.window_relaunch_command(int(w.winId())) == f'"{exe}"'
    w.close()
    app.processEvents()


def test_no_identity_outside_the_portable(monkeypatch):
    monkeypatch.setattr(winshell, "launcher", lambda: None)
    assert not winshell.set_window_identity(0)
