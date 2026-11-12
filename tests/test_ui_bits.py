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
    assert group_of("Maple  ·  US female, Kokoro 1.1", "kokoro:af_maple") == "female"     # comma after gender
    assert group_of("Ann (fast)  ·  UK, Kokoro copy of a cloned voice", "fast:ann") == "other"


def test_every_kokoro_voice_lands_in_a_gender_column():
    from app.tts import voices as V
    for v in V.kokoro_voices():
        assert group_of(v.label, v.id) == v.gender, v.label


def test_popup_details_are_short():
    from app.ui.voice_picker import accent_of, short_detail
    assert short_detail("US female", "female") == "US"
    assert short_detail("US female, Kokoro 1.1", "female") == "US, Kokoro 1.1"
    assert short_detail("UK male", "male") == "UK"
    assert short_detail("cloned voice", "other") == ""
    assert short_detail("cloned voice, trained", "other") == "trained"
    assert short_detail("UK, Kokoro copy of a cloned voice", "other") == "UK"
    assert short_detail("expressive, built-in", "other") == "expressive, built-in"
    assert accent_of("US, Kokoro 1.1") == "US" and accent_of("UK") == "UK" and accent_of("") == ""


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


def test_voice_popup_fits_long_names():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    app = QApplication.instance() or QApplication([])
    if not isinstance(app, QApplication):
        pytest.skip("needs a QApplication")
    from app.ui.voice_picker import VoiceCombo, _VoicePopup
    combo = VoiceCombo()
    long = "Bartholomew Montgomery-Fitzwilliam"
    for label, vid in ((f"{long}  ·  cloned voice, trained", "clone:b"),
                       (f"{long} (fast)  ·  UK, Kokoro copy of a cloned voice", "fast:b"),
                       ("Maple  ·  US female, Kokoro 1.1", "kokoro:af_maple")):
        combo.addItem(label, vid)
    pop = _VoicePopup(combo)
    pop.setAttribute(Qt.WA_DontShowOnScreen, True)
    for b in pop.findChildren(QPushButton):
        shown = [lab for lab in b.findChildren(QLabel) if not lab.isHidden()]
        used = sum(lab.fontMetrics().horizontalAdvance(lab.text()) for lab in shown)
        assert used <= b.width() - 20, [lab.text() for lab in shown]         # nothing cut off at the edge
        assert b.toolTip().startswith(shown[0].text().rstrip("…"))           # full text in the tooltip
    maple = next(b for b in pop.findChildren(QPushButton) if b.toolTip().startswith("Maple"))
    name, detail = maple.findChildren(QLabel)
    fm = detail.fontMetrics()
    if fm.horizontalAdvance("Maple") + 12 + fm.horizontalAdvance("US, Kokoro 1.1") < maple.width() - 30:
        assert (name.text(), detail.text()) == ("Maple", "US, Kokoro 1.1")   # short ones are left whole


def test_titles_sort_as_written_or_without_articles(monkeypatch):
    from app.library import views
    rows = [{"title": t, "series": t} for t in ("The Beginning After the End", "Bungo Stray Dogs", "A Silent Voice",
                                                 "Tearmoon Empire", "86--EIGHTY-SIX")]
    titles = lambda rs: [r["title"] for r in rs]  # noqa: E731
    monkeypatch.setattr(views, "IGNORE_ARTICLES", False)
    assert titles(views.apply_sort(rows, "Title")) == ["86--EIGHTY-SIX", "A Silent Voice", "Bungo Stray Dogs",
                                                      "Tearmoon Empire", "The Beginning After the End"]
    assert [g[0] for g in views.series_groups(rows)][-1] == "The Beginning After the End"
    views.set_ignore_articles(True)
    try:
        assert titles(views.apply_sort(rows, "Title")) == ["86--EIGHTY-SIX", "The Beginning After the End",
                                                          "Bungo Stray Dogs", "A Silent Voice", "Tearmoon Empire"]
    finally:
        views.set_ignore_articles(False)


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


def test_media_glyphs_map_to_drawn_shapes():
    from app.ui import theme
    from app.ui.widgets import SHAPES, shape_for_glyph, shape_path
    for kind in SHAPES:
        assert shape_for_glyph(theme.I[kind]) == kind
        assert not shape_path(kind).isEmpty()
    assert shape_for_glyph(theme.I["list"]) is None          # other icons stay font glyphs
