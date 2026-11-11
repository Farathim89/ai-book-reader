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
    assert 0.6 * p.pauses["paragraph"] < p.pause(0) < 1.2 * p.pauses["paragraph"]   # short sentence: a bit less
    assert p.pause(1) == p.pauses["chapter"]


def test_pauses_follow_punctuation_like_a_narrator():
    b = _book(["Is it you? She waited... Then she spoke for a long while about the old house, the garden, the "
               "lamps in the windows and the letters that never came, until the fire burned low. It was late, so "
               "she went to bed. Oh. Fine."])
    p = VoicePlanner(b, "N", {})
    base = p.pauses["sentence"]
    question, dots, long_one, plain, tiny = (p.pause(u) for u in range(5))
    assert dots > plain and dots > question and long_one > tiny
    assert all(0.5 * base < x < 1.7 * base for x in (question, dots, long_one, plain, tiny))
    assert p.pause(1) == VoicePlanner(b, "N", {}).pause(1)          # the same every time (exports resume)


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
    # more men than male voices: the extras share with the least talkative men, never the lead
    many = {f"Man{i}": {"gender": "male", "lines": 100 - i} for i in range(16)}
    cast = auto_cast(many, "kokoro:af_heart")
    lead_voice = cast["Man0"]
    assert all(cast[f"Man{i}"] != lead_voice for i in range(1, 16))


def test_castbook_aliases():
    cb = CastBook({})
    cb.add("Elizabeth Bennet", "female", ["Lizzy"])
    assert cb.resolve("lizzy") == "Elizabeth Bennet"
    assert cb.resolve("Elizabeth") == "Elizabeth Bennet"
    assert cb.resolve("Unknown") is None
    assert cb.add("Mr. Darcy", "male", []) == "Mr. Darcy"
    assert cb.resolve("Darcy") == "Mr. Darcy"


def test_infer_genders_from_titles_and_pronouns():
    from app.ai.characters import infer_genders
    b = _book(["“Hello,” he said.", "“Yes?” he asked, and he smiled.", "“No,” she said.",
               "“Why?” asked her aunt.", "“Go.”"])
    speakers = {0: "Ron", 1: "Ron", 2: "Aunt Petunia", 3: "Aunt Petunia", 4: "Kid"}
    data = {"characters": {"Ron": {"gender": "unknown", "aliases": [], "lines": 2},
                           "Aunt Petunia": {"gender": "unknown", "aliases": [], "lines": 2},
                           "Kid": {"gender": "unknown", "aliases": [], "lines": 1}},
            "speakers": {str(k): v for k, v in speakers.items()}}
    infer_genders(b, data)
    g = {n: i["gender"] for n, i in data["characters"].items()}
    assert g == {"Ron": "male", "Aunt Petunia": "female", "Kid": "unknown"}


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


def test_audiobook_path_layouts(tmp_path):
    from app.audio.export import audiobook_path
    p = audiobook_path(tmp_path, "Asato Asato", "Run Through the Battlefront", "86--EIGHTY-SIX", 2.0)
    assert p == tmp_path / "Asato Asato" / "86--EIGHTY-SIX" / "2 - Run Through the Battlefront" / "Run Through the Battlefront.m4b"
    assert audiobook_path(tmp_path, "Eiko Kadono", "Kiki", None, None) == tmp_path / "Eiko Kadono" / "Kiki" / "Kiki.m4b"
    assert audiobook_path(tmp_path, "A", "T", "S", 4.5, abs_layout=False) == tmp_path / "A - S 4.5 - T.m4b"
    assert audiobook_path(tmp_path, "A", "T", None, None, abs_layout=False) == tmp_path / "A - T.m4b"


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
    lib.update(bid, position=5, total=11)
    assert lib.get(bid)["progress"] == 0.5 and not lib.get(bid)["finished"]
    lib.update(bid, position=10)
    assert lib.get(bid)["finished"]
    lib.update_settings(bid, narrator="kokoro:am_adam")
    assert lib.get(bid)["settings"]["narrator"] == "kokoro:am_adam"
    b1 = store.open_book(bid, str(p))
    b2 = store.open_book(bid, str(p))      # from the parse cache
    assert b1.unit_count() == b2.unit_count() and b2.unit_text(4) == b1.unit_text(4)
    lib.close()


def test_details_series_description(tmp_path):
    import shutil
    from app.formats.details import read_details, series_from_title
    d = tmp_path / "Asato Asato" / "86 Vol 1 (3)"
    d.mkdir(parents=True)
    p = shutil.copy(samples.make_epub(tmp_path / "x.epub"), d / "book.epub")
    (d / "metadata.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>86, Vol. 1</dc:title>'
        "<dc:description>&lt;p&gt;A war &amp;amp; peace.&lt;/p&gt;</dc:description><dc:publisher>Yen On</dc:publisher>"
        "<dc:date>2017-03-21T00:00:00+00:00</dc:date>"
        '<meta name="calibre:series" content="86--EIGHTY-SIX"/><meta name="calibre:series_index" content="1"/>'
        "</metadata></package>", encoding="utf-8")
    det = read_details(str(p))
    assert det["series"] == "86--EIGHTY-SIX" and det["series_index"] == 1.0
    assert det["publisher"] == "Yen On" and det["year"] == 2017 and det["description"] == "A war & peace."
    assert series_from_title("A Gentle Noble's Vacation Recommendation v03 (2021) (Digital)") == \
        ("A Gentle Noble's Vacation Recommendation", 3.0)
    assert series_from_title("How a Realist Hero Rebuilt the Kingdom, Vol. 12") == \
        ("How a Realist Hero Rebuilt the Kingdom", 12.0)
    assert series_from_title("Kiki's Delivery Service") is None


def test_libraries_mapping():
    from app.library.libraries import OTHER, add_folder, est_seconds, fmt_duration, library_of, libraries
    s = {"library_folders": [r"H:\-=Books=-\EBooks"]}
    assert libraries(s) == [{"name": "EBooks", "folders": [r"H:\-=Books=-\EBooks"]}]
    add_folder(s, r"H:\-=Books=-\Manga")
    add_folder(s, r"D:\Downloads\Some Manga", "Manga")
    libs = libraries(s)
    assert [l["name"] for l in libs] == ["EBooks", "Manga"] and len(libs[1]["folders"]) == 2
    assert library_of(r"D:\Downloads\Some Manga\v01.cbz", libs) == "Manga"
    assert library_of(r"H:\-=Books=-\EBooks\A\b.epub", libs) == "EBooks"
    assert library_of(r"C:\elsewhere\b.epub", libs) == OTHER
    assert fmt_duration(est_seconds(17 * 3600 * 7 + 17 * 240)) == "7 hr 4 min"


def _rows():
    def r(i, title, author="A", series=None, idx=None, started=False, finished=False, opened=0, added=0, lib="L1"):
        return {"id": i, "title": title, "author": author, "series": series, "series_index": idx, "fmt": "epub",
                "started": started, "finished": finished, "opened": opened, "added": added, "library": lib,
                "progress": 0.5 if started else 0.0, "settings": {}, "chars": i * 100}
    return [r(1, "The Vol 10", "Asato Asato", "86", 10, added=5),
            r(2, "86 Vol 2", "Asato Asato", "86", 2, finished=True, opened=50, added=3),
            r(3, "86 Vol 1", "Asato Asato", "86", 1, finished=True, opened=40, added=2),
            r(4, "86 Vol 3", "Asato Asato", "86", 3, added=4),
            r(5, "Kiki", "Eiko Kadono, Emily Balistrieri", started=True, opened=60, added=1, lib="L2"),
            r(6, "Earthsea 1", "Ursula K. Le Guin", "Earthsea", 1, started=True, opened=10, added=6)]


def test_views_sort_filter_groups():
    from app.library import views
    rows = _rows()
    assert [r["id"] for r in views.apply_sort(rows, "Series")][:4] == [3, 2, 4, 1]   # 1, 2, 3, 10 (natural)
    assert [r["id"] for r in views.apply_filter(rows, "In progress")] == [5, 6]
    assert [r["id"] for r in views.in_library(rows, "L2")] == [5]
    assert [r["id"] for r in views.search(rows, "kiki kadono")] == [5]
    series = dict(views.series_groups(rows))
    assert [b["id"] for b in series["86"]] == [3, 2, 4, 1]
    authors = dict(views.author_groups(rows))
    assert "Emily Balistrieri" in authors and len(authors["Asato Asato"]) == 4
    shelves = dict(views.home_shelves(rows))
    assert [b["id"] for b in shelves["Continue Reading"]] == [5, 6]
    assert [b["id"] for b in shelves["Continue Series"]] == [4]       # next 86 volume after 2 finished ones
    assert [b["id"] for b in shelves["Read Again"]] == [2, 3]
    assert views.fmt_index(4.0) == "4" and views.fmt_index(4.5) == "4.5"


def test_search_ranking():
    from app.ui.search import match_names, rank_books
    rows = _rows()
    assert [r["id"] for r in rank_books(rows, "86")][:3] == [3, 2, 4]   # titles starting with "86", in volume order
    assert [r["id"] for r in rank_books(rows, "vol 10")] == [1]
    assert match_names([("Earthsea", 6), ("86", 13), ("Sea of Stars", 2)], "sea") == [("Sea of Stars", 2), ("Earthsea", 6)]
    assert match_names([("Asato Asato", 4)], "asato a") == [("Asato Asato", 4)]


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
