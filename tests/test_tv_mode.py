import pytest

from app.ui.gamepad import XINPUT_GAMEPAD, pressed_names


def test_controller_buttons_and_sticks():
    pad = XINPUT_GAMEPAD()
    pad.wButtons = 0x1000 | 0x0200          # A + RB
    pad.sThumbLX = 30000                    # left stick right
    pad.sThumbLY = -30000                   # ... and down
    pad.bRightTrigger = 200
    assert pressed_names(pad) == {"a", "rb", "right", "down", "rt"}
    assert pressed_names(XINPUT_GAMEPAD()) == set()


class StubTv:
    zone = "content"

    def grid_cols(self, item_w: int) -> int:
        return 3


def test_tv_page_moves_like_a_remote():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    if not isinstance(app, QApplication):
        pytest.skip("needs a QApplication")
    from app.ui.tv_mode import TvButton, TvPage
    hits = []
    page = TvPage(StubTv(), "Test")
    page.setAttribute(Qt.WA_DontShowOnScreen, True)
    page.add_grid([TvButton(str(n), lambda n=n: hits.append(n), width=100) for n in range(7)])   # rows: 3, 3, 1
    page.add_buttons([TvButton("Wide", lambda: hits.append("wide"), width=300)])
    page.resize(1200, 900)
    page.show()
    page.set_pos(0, 0)
    page.handle("right")
    page.handle("right")
    page.handle("right")                      # stops at the end of the line
    assert page.pos == (0, 2)
    page.handle("down")
    page.handle("down")                       # the last grid line has one item: nearest is it
    assert page.pos == (2, 0)
    page.handle("down")
    page.handle("ok")
    assert hits == ["wide"]
    page.handle("up")
    page.handle("up")
    assert page.pos[0] == 1
    page.set_pos(1, 0)
    assert page.handle("left") == "menu"      # left from the first item goes to the side menu


def test_title_labels_setting_migrates_from_show_titles(tmp_path):
    import json
    from app.config import Settings
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"show_titles": True, "settings_version": 2}), encoding="utf-8")
    assert Settings(p)["title_bar"] == "cover"
    p.write_text(json.dumps({"show_titles": False, "settings_version": 2}), encoding="utf-8")
    assert Settings(p)["title_bar"] == "under"
