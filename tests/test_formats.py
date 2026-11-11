import pytest

from app.formats import detect_format, load_book, read_meta
from app.formats.textproc import find_quotes, speech_text, split_for_tts, split_sentences
from tests import samples


def sents(text):
    return [text[s:e] for s, e in split_sentences(text)]


def test_sentence_split_basic():
    assert sents("Hello there. How are you? I am fine!") == ["Hello there.", "How are you?", "I am fine!"]


def test_sentence_split_abbrev_and_initials():
    t = "Mr. Smith met Dr. Jones at 9 a.m. Then J. R. R. Tolkien arrived."
    assert sents(t) == ["Mr. Smith met Dr. Jones at 9 a.m. Then J. R. R. Tolkien arrived."] or \
        sents(t) == ["Mr. Smith met Dr. Jones at 9 a.m.", "Then J. R. R. Tolkien arrived."]
    assert "Mr." in sents(t)[0] and "Smith" in sents(t)[0]


def test_sentence_split_dotted_initials():
    assert sents("Written by J.K. Rowling. The end.") == ["Written by J.K. Rowling.", "The end."]


def test_refine_chapters_splits_long_single_chapter():
    from app.formats.chapters import refine_chapters
    from app.formats.model import Chapter, Paragraph
    body = ["Word " * 60 + "end."] * 120
    paras = [Paragraph("Contents"), Paragraph("1. What Torren Saw ..........  5")]
    for n in range(1, 4):
        paras += [Paragraph(f"CHAPTER {n} - Title {n}")] + [Paragraph(t) for t in body]
    out = refine_chapters([Chapter("Book", paras)])
    assert [c.title for c in out] == ["Book – CHAPTER 1 - Title 1", "CHAPTER 2 - Title 2", "CHAPTER 3 - Title 3"] \
        or [c.title for c in out][-3:] == ["CHAPTER 1 - Title 1", "CHAPTER 2 - Title 2", "CHAPTER 3 - Title 3"]
    # no headings at all -> ~5000-word parts
    out = refine_chapters([Chapter("Book", [Paragraph(t) for t in body * 3])])
    assert len(out) >= 4 and out[0].title == "Book – part 1"


def test_calibre_metadata(books, tmp_path):
    import shutil
    from app.formats import read_meta
    d = tmp_path / "Calibre Author" / "Some Book (12)"
    d.mkdir(parents=True)
    p = shutil.copy(books["pdf"], d / "Some Book - Calibre Author.pdf")
    (d / "metadata.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Real Title</dc:title>'
        "<dc:creator>Real Author</dc:creator></metadata></package>", encoding="utf-8")
    t, a, c = read_meta(str(p))
    assert (t, a) == ("Real Title", "Real Author") and c


def test_sentence_split_dialogue():
    t = "“Stop!” he shouted. “Why?” She ran."
    assert sents(t) == ["“Stop!” he shouted.", "“Why?”", "She ran."]


def test_quotes_curly_and_straight():
    t = "“You’re late,” said Anna. “The others left.”"
    spans = find_quotes(t)
    assert [t[s:e] for s, e in spans] == ["“You’re late,”", "“The others left.”"]
    t2 = 'He said "go" and "stay".'
    assert [t2[s:e] for s, e in find_quotes(t2)] == ['"go"', '"stay"']


def test_quotes_british_single_and_apostrophes():
    t = "‘I don’t know,’ said Tom. ‘It’s late.’"
    assert [t[s:e] for s, e in find_quotes(t)] == ["‘I don’t know,’", "‘It’s late.’"]


def test_unclosed_quote_runs_to_end():
    t = "“And so we went on, and on"
    assert find_quotes(t) == [(0, len(t))]


def test_speech_text_caps_and_marks():
    assert speech_text("WHERE ARE YOU GOING?") == "Where are you going?"
    assert speech_text("“Hello,” she said—quietly.") == "Hello, she said - quietly."


def test_split_for_tts():
    long = ", ".join(["word " * 10] * 10)
    parts = split_for_tts(long, 120)
    assert all(len(p) <= 120 for p in parts) and len(parts) > 1


@pytest.fixture(scope="module")
def books(tmp_path_factory):
    return samples.build_all(tmp_path_factory.mktemp("books"))


@pytest.mark.parametrize("fmt", ["epub", "txt", "docx", "rtf", "fb2", "pdf"])
def test_text_formats(books, fmt):
    b = load_book(str(books[fmt]))
    assert detect_format(books[fmt]) == fmt
    assert b.title == "The Flooded House"
    assert b.author == "Jane Example"
    assert len(b.chapters) == 2, [c.title for c in b.chapters]
    allt = " ".join(p.text for c in b.chapters for p in c.paragraphs)
    assert "Harlow" in allt and "leather bag" in allt
    assert "licence text" not in allt
    assert b.unit_count() >= 10
    # dialogue found in chapter one
    assert sum(len(p.quotes) for p in b.chapters[0].paragraphs) >= 3


def test_epub_toc_titles_and_cover(books):
    b = load_book(str(books["epub"]))
    assert [c.title for c in b.chapters] == ["Chapter One: Rain", "Chapter Two: Morning"]
    assert b.cover and b.cover[:4] == b"\x89PNG"
    t, a, c = read_meta(str(books["epub"]))
    assert (t, a) == ("The Flooded House", "Jane Example") and c


def _with_encryption(src, dst, uri):
    import zipfile
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for n in zin.namelist():
            zout.writestr(n, zin.read(n))
        zout.writestr("META-INF/encryption.xml",
                      '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                      'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>'
                      '<enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
                      f'<enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>'
                      "</enc:EncryptedData></encryption>")
    return dst


def test_epub_font_obfuscation_is_not_drm(books, tmp_path):
    from app.formats import BookError
    ok = _with_encryption(books["epub"], tmp_path / "fonts.epub", "OEBPS/fonts/Serif.otf")
    assert load_book(str(ok)).unit_count() > 0
    bad = _with_encryption(books["epub"], tmp_path / "drm.epub", "OEBPS/text/ch1.xhtml")
    with pytest.raises(BookError, match="DRM"):
        load_book(str(bad))


def test_cbz_without_and_with_ocr(books):
    b = load_book(str(books["cbz"]))
    assert b.kind == "comic" and len(b.pages) == 2 and b.title == "Night Walk #1"
    assert b.ocr_pages_needed == [0, 1] and b.unit_count() == 0
    ocr = {0: [{"text": "Where are you going?", "box": [0.1, 0.1, 0.7, 0.3]}],
           1: [{"text": "Home. It's late.", "box": [0.1, 0.1, 0.7, 0.3]}]}
    b2 = load_book(str(books["cbz"]), ocr)
    assert b2.unit_count() == 3 and b2.ocr_pages_needed == []
    assert b2.unit_para(0).page == 0 and b2.unit_para(2).page == 1


def test_roundtrip_dict(books):
    from app.formats.model import Book
    b = load_book(str(books["epub"]))
    b2 = Book.from_dict(b.to_dict())
    b2.build_index()
    assert b2.unit_count() == b.unit_count()
    assert b2.unit_text(3) == b.unit_text(3)
