import sqlite3

import pytest

from app.ui.voice_picker import group_of, split_label


def test_voice_labels_are_grouped_by_gender():
    assert split_label("Heart  \u00b7  US female") == ("Heart", "US female")
    assert group_of("Heart  \u00b7  US female", "kokoro:af_heart") == "female"
    assert group_of("Fable  \u00b7  UK male", "kokoro:bm_fable") == "male"
    assert group_of("Chatterbox  \u00b7  expressive, built-in", "chatterbox:default") == "other"
    assert group_of("Grandpa  \u00b7  cloned", "clone:grandpa") == "other"
    assert group_of("Narrator voice", "") == "special"


def test_voice_popup_lists_columns_and_picks(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton
    app = QApplication.instance() or QApplication([])
    if not isinstance(app, QApplication):
        pytest.skip("needs a QApplication")
    from app.ui.voice_picker import VoiceCombo, _VoicePopup
    combo = VoiceCombo()
    combo.addItem("Narrator voice", "")
    for label, vid in (("Heart  \u00b7  US female", "kokoro:af_heart"), ("Emma  \u00b7  UK female", "kokoro:bf_emma"),
                       ("Adam  \u00b7  US male", "kokoro:am_adam"), ("Chatterbox  \u00b7  built-in", "chatterbox:default")):
        combo.addItem(label, vid)
    picked = []
    combo.activated.connect(lambda i: picked.append(combo.itemData(i)))
    pop = _VoicePopup(combo)
    pop.setAttribute(Qt.WA_DontShowOnScreen, True)
    tips = [b.toolTip() for b in pop.findChildren(QPushButton)]
    assert len(tips) == 5 and tips[0] == "Narrator voice"
    next(b for b in pop.findChildren(QPushButton) if b.toolTip().startswith("Adam")).click()
    assert picked == ["kokoro:am_adam"] and combo.currentData() == "kokoro:am_adam"


def test_covers_are_found_after_the_app_folder_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    from app.library.db import Library
    lib = Library()
    with lib.lock:
        lib.conn.execute("INSERT INTO books (id, path, title, cover) VALUES (7, 'x.epub', 'X', ?)",
                         (r"D:\old place\data\covers\7.jpg",))
        lib.conn.commit()
    (tmp_path / "data" / "covers").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "covers" / "7.jpg").write_bytes(b"JPG")
    lib.conn.close()
    lib = Library()                                        # repaired on open
    assert lib.get(7)["cover"] == str(tmp_path / "data" / "covers" / "7.jpg")
    assert lib.repair_covers() == 0
    con = sqlite3.connect(str(tmp_path / "data" / "library.db"))
    assert con.execute("SELECT cover FROM books WHERE id=7").fetchone()[0].endswith("7.jpg")
