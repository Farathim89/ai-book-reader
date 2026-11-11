import io
import os
import shutil

import pytest

from app.ai.characters import CastBook, _chunks
from app.ai.ocr import fix_spacing, group_bubbles, lines_to_paragraphs, match_boxes, reading_order
from app.audio.export import ffmeta, safe_filename
from app.formats import load_book
from app.formats.model import Book, Chapter, Paragraph
from app.tts.planner import VoicePlanner, auto_cast
from tests import samples


def _book(texts):
    b = Book(path="x", fmt="txt", title="T", chapters=[Chapter("C", [Paragraph(t) for t in texts])])
    b.build_index()
    return b


def test_planner_splits_dialogue_between_voices():
    b = _book(["\u201cUpstairs,\u201d Harlow replied, \u201cin the hall.\u201d"])
    b.chapters[0].paragraphs[0].quotes[0].speaker = "Harlow"
    b.chapters[0].paragraphs[0].quotes[1].speaker = "Harlow"
    p = VoicePlanner(b, "N", {"Harlow": "H"}, use_characters=True)
    assert p.runs(0) == [("H", "\u201cUpstairs,\u201d"), ("N", " Harlow replied, "), ("H", "\u201cin the hall.\u201d")]
    p.use_characters = False
    assert p.runs(0) == [("N", b.unit_text(0))]


def test_planner_unknown_speaker_uses_narrator_and_pauses():
    b = _book(["\u201cHello.\u201d", "Bye."])
    p = VoicePlanner(b, "N", {}, use_characters=True)
    assert p.runs(0) == [("N", "\u201cHello.\u201d")]
    assert p.pause(0) == p.pauses["paragraph"] and p.pause(1) == p.pauses["chapter"]


def test_auto_cast_by_gender_and_avoids_narrator():
    chars = {"Anna": {"gender": "female", "lines": 9}, "Tom": {"gender": "male", "lines": 5},
             "Kid": {"gender": "unknown", "lines": 1}}
    cast = auto_cast(chars, "kokoro:af_heart")
    assert cast["Anna"].startswith("kokoro:") and cast["Anna"] != "kokoro:af_heart"
    assert "_f" in cast["Anna"] or cast["Anna"].split(":")[1][1] == "f"
    assert cast["Tom"].split(":")[1][1] == "m"
    assert len(set(cast.values())) == 3
    # existing choices are kept
    assert auto_cast(chars, "kokoro:af_heart", {"Anna": "clone:x"})["Anna"] == "clone:x"


def test_castbook_aliases():
    cb = CastBook({})
    cb.add("Elizabeth Bennet", "female", ["Lizzy"])
    assert cb.resolve("lizzy") == "Elizabeth Bennet"
    assert cb.resolve("Elizabeth") == "Elizabeth Bennet"
    assert cb.resolve("Unknown") is None
    assert cb.add("Mr. Darcy", "male", []) == "Mr. Darcy"
    assert cb.resolve("Darcy") == "Mr. Darcy"


def test_character_chunks_mark_quotes(tmp_path):
    b = load_book(str(samples.make_epub(tmp_path / "b.epub")))
    ctx, marked, qids = next(_chunks(b, 0))
    assert qids == [0, 1, 2]
    assert "[Q0]\u201cYou\u2019re late,\u201d" in " ".join(marked)


def test_bubble_grouping_and_order():
    lines = [([500, 100, 700, 130], "HEY"), ([500, 135, 700, 165], "THERE"),        # bubble right
             ([100, 110, 300, 140], "HELLO"), ([100, 600, 300, 630], "BYE")]       # left, lower
    groups = group_bubbles(lines)
    assert sorted(t for _, t in groups) == ["BYE", "HELLO", "HEY THERE"]
    assert [t for _, t in reading_order(groups, rtl=False)] == ["HELLO", "HEY THERE", "BYE"]
    assert [t for _, t in reading_order(groups, rtl=True)] == ["HEY THERE", "HELLO", "BYE"]


def test_match_boxes_and_spacing():
    groups = [([0, 0, 10, 10], "WHEREARE YOU GOING?"), ([0, 20, 10, 30], "HOME. IT'S LATE.")]
    boxes = match_boxes(["Home. It's late.", "Where are you going?", "Bang"], groups)
    assert boxes == [[0, 20, 10, 30], [0, 0, 10, 10], None]
    assert fix_spacing("WHEREARE YOU GOING?") == "WHERE ARE YOU GOING?"
    assert fix_spacing("HOME.IT'S LATE") == "HOME. IT'S LATE"
    assert fix_spacing("IT'SMADE OF THERE'SNO YOU'REIN") == "IT'S MADE OF THERE'S NO YOU'RE IN"
    from app.ai.ocr import _is_speech
    assert not _is_speech("13") and not _is_speech("回") and _is_speech("WH-")


def test_vlm_bubbles_order_ids_and_safety_net():
    from PIL import Image
    from app.ai.ocr import PageReader

    class FakeOllama:
        def chat_json(self, model, system, user, schema=None, images=None, **kw):
            assert "1: HELLO" in user and images
            # model reorders, fixes text, skips the SFX (3) - and forgets balloon 2
            return {"bubbles": [{"id": 4, "text": "Bye now."}, {"id": 1, "text": "Hello."}, {"id": 9, "text": "x"}]}

    buf = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buf, "PNG")
    groups = [([10, 10, 100, 40], "HELLO"), ([10, 60, 200, 90], "WHERE ARE YOU GOING?"),
              ([300, 10, 340, 30], "BAM"), ([10, 300, 100, 330], "BYE NOW.")]
    out = PageReader(FakeOllama(), "m")._vlm_bubbles(buf.getvalue(), groups, rtl=False)
    assert out == [(3, "Bye now."), (0, "Hello."), (1, "WHERE ARE YOU GOING?")]


def test_lines_to_paragraphs():
    lines = [([50, 100, 500, 120], "The first para-"), ([50, 124, 500, 144], "graph goes on."),
             ([80, 170, 500, 190], "Second paragraph.")]
    assert lines_to_paragraphs(lines) == ["The first paragraph goes on.", "Second paragraph."]


def test_ffmeta_chapters():
    m = ffmeta("A=B", "Me", [("One", 10.0), ("Two; #2", 5.5)])
    assert "title=A\\=B" in m and "START=10000" in m and "END=15500" in m and "title=Two\\; \\#2" in m
    assert safe_filename('a:b/c?') == "a_b_c_"


def test_library_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    from app.library.db import Library
    from app.library import store
    lib = Library(tmp_path / "lib.db")
    p = samples.make_epub(tmp_path / "Jane Example - The Flooded House.epub")
    bid = lib.add(str(p))
    assert bid and lib.add(str(p)) is None
    r = lib.get(bid)
    assert r["title"] == "The Flooded House" and r["cover"] and os.path.exists(r["cover"])
    lib.update(bid, position=5, total=10)
    assert lib.get(bid)["progress"] == 0.5
    lib.update_settings(bid, narrator="kokoro:am_adam")
    assert lib.get(bid)["settings"]["narrator"] == "kokoro:am_adam"
    b1 = store.open_book(bid, str(p))
    b2 = store.open_book(bid, str(p))      # from the parse cache
    assert b1.unit_count() == b2.unit_count() and b2.unit_text(4) == b1.unit_text(4)
    lib.close()


def test_scan_folder_prefers_one_format_per_book(tmp_path):
    from app.library.db import scan_folder
    d = tmp_path / "Author" / "Book (1)"
    d.mkdir(parents=True)
    for name in ("Book.pdf", "Book.mobi", "Book.epub", "Book.azw3", "Other.txt", "cover.jpg", "metadata.opf"):
        (d / name).write_bytes(b"x")
    assert sorted(os.path.basename(p) for p in scan_folder(str(tmp_path))) == ["Book.epub", "Other.txt"]


def make_scanned_pdf(path):
    import fitz
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("georgia.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    y = 200
    for line in ["The storm came over the hills at dusk.", "Nobody in the village was ready for it.", "",
                 "By morning the bridge was gone."]:
        if line:
            d.text((150, y), line, fill="black", font=font)
        y += 60
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))
    return path


def test_scanned_pdf_needs_ocr_then_reads(tmp_path):
    from app.ai.ocr import PageReader
    from app.formats.pdf import render_page_png
    p = make_scanned_pdf(tmp_path / "scan.pdf")
    b = load_book(str(p))
    assert b.ocr_pages_needed == [0] and b.unit_count() == 0
    paras = PageReader(None, None).scan_page(render_page_png(str(p), 0))
    text = " ".join(paras).lower()
    assert "storm" in text and "bridge" in text
    b2 = load_book(str(p), {0: paras})
    assert b2.unit_count() >= 3 and b2.ocr_pages_needed == []


@pytest.mark.skipif(not shutil.which("ebook-convert") and not os.path.exists(r"C:\Program Files\Calibre2\ebook-convert.exe"),
                    reason="Calibre not installed")
@pytest.mark.parametrize("ext", ["azw3", "mobi"])
def test_kindle_formats_via_calibre(tmp_path, monkeypatch, ext):
    import subprocess
    from app.formats.kindle import calibre_tool
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    src = samples.make_epub(tmp_path / "src.epub")
    out = tmp_path / f"Jane Example - The Flooded House.{ext}"
    subprocess.run([calibre_tool("ebook-convert"), str(src), str(out)], capture_output=True, check=True)
    b = load_book(str(out))
    assert b.fmt == ext and b.title == "The Flooded House"
    allt = " ".join(p.text for c in b.chapters for p in c.paragraphs)
    assert "Harlow" in allt and "leather bag" in allt
    assert b.quote_count() >= 5
