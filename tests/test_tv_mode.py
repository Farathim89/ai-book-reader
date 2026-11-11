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


def _ds4(lx=128, ly=128, ry=128, b1=0x08, b2=0, l2=0, r2=0, bt_full=False) -> bytes:
    """A DualShock 4 input report: USB / Bluetooth basic (id 0x01) or Bluetooth full (0x11)."""
    body = bytes([lx, ly, 128, ry, b1, b2, 0, l2, r2]) + bytes(55)
    return bytes([0x11, 0xC0, 0x00]) + body if bt_full else bytes([0x01]) + body


def _dualsense(lx=128, ly=128, ry=128, b1=0x08, b2=0, l2=0, r2=0, bt_full=False) -> bytes:
    """A DualSense input report: USB (0x01, 64 bytes) or Bluetooth full (0x31)."""
    body = bytes([lx, ly, 128, ry, l2, r2, 0, b1, b2]) + bytes(55)
    return bytes([0x31, 0x10]) + body if bt_full else bytes([0x01]) + body


def test_playstation_reports_map_to_the_same_buttons():
    from app.ui.ps_controller import parse_report
    # nothing pressed (hat 8 = released, sticks centred)
    assert parse_report(_ds4(), False, 64) == (set(), 0)
    # cross + hat up-right + L1 + Options, over USB and over Bluetooth's full report
    want = ({"a", "up", "right", "lb", "start"}, 0)
    assert parse_report(_ds4(b1=0x20 | 0x01, b2=0x01 | 0x20), False, 64) == want
    assert parse_report(_ds4(b1=0x20 | 0x01, b2=0x01 | 0x20, bt_full=True), False, 547) == want
    # circle, R2 pulled, left stick left, right stick up = volume up
    names, vol = parse_report(_ds4(lx=10, ry=5, b1=0x40 | 0x08, r2=250), False, 64)
    assert names == {"b", "rt", "left"} and vol == 1
    # DualSense: triangle + square, Create, left stick down, over USB and Bluetooth
    want = ({"y", "x", "back", "down"}, -1)
    assert parse_report(_dualsense(ly=250, ry=250, b1=0x80 | 0x10 | 0x08, b2=0x10), True, 64) == want
    assert parse_report(_dualsense(ly=250, ry=250, b1=0x80 | 0x10 | 0x08, b2=0x10, bt_full=True), True, 78) == want
    # DualSense Bluetooth basic report uses the DualShock layout
    assert parse_report(_ds4(b1=0x20 | 0x08), True, 78) == ({"a"}, 0)
    assert parse_report(b"\x05" + bytes(30), False, 64) is None and parse_report(b"", False, 64) is None


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
