"""Build small sample books in every supported format (used by the tests)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

CH1 = [
    "The rain had not stopped for three days when Mr. Harlow finally opened the door.",
    "\u201cYou\u2019re late,\u201d said Anna. \u201cThe others left an hour ago.\u201d",
    "He shrugged and hung his coat by the fire. \u201cThe roads were flooded. What did I miss?\u201d",
    "Anna laughed. It was the first time in weeks that anyone had laughed in that house.",
]
CH2 = [
    "Morning came grey and quiet. Dr. Reyes arrived at 9 a.m. with a leather bag.",
    "\u201cWhere is the patient?\u201d she asked.",
    "\u201cUpstairs,\u201d Harlow replied, \u201cin the room at the end of the hall.\u201d",
]


def make_epub(path: Path) -> Path:
    def xhtml(title, paras, anchor=None):
        body = "".join(f"<p>{p}</p>" for p in paras)
        a = f' id="{anchor}"' if anchor else ""
        return (f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                f"<head><title>{title}</title></head><body><h1{a}>{title}</h1>{body}</body></html>")

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>")
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Flooded House</dc:title>'
                   "<dc:creator>Jane Example</dc:creator></metadata><manifest>"
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                   '<item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="cov" href="img/cover.png" media-type="image/png" properties="cover-image"/>'
                   '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/nav.xhtml",
                   '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                   '<nav epub:type="toc"><ol><li><a href="text/ch1.xhtml">Chapter One: Rain</a></li>'
                   '<li><a href="text/ch2.xhtml">Chapter Two: Morning</a></li></ol></nav></body></html>')
        z.writestr("OEBPS/text/ch1.xhtml", xhtml("Chapter One", CH1))
        z.writestr("OEBPS/text/ch2.xhtml", xhtml("Chapter Two", CH2))
        z.writestr("OEBPS/img/cover.png", _png(200, 300, (40, 60, 120)))
    return path


def make_txt(path: Path) -> Path:
    lines = ["Title: The Flooded House", "Author: Jane Example", "",
             "*** START OF THE PROJECT GUTENBERG EBOOK THE FLOODED HOUSE ***", "", "CHAPTER I.", ""]
    for p in CH1:
        words = p.split()
        lines += [" ".join(words[:8]), " ".join(words[8:]), ""]
    lines += ["CHAPTER II.", ""]
    for p in CH2:
        lines += [p, ""]
    lines += ["*** END OF THE PROJECT GUTENBERG EBOOK ***", "licence text that must not be read"]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def make_docx(path: Path) -> Path:
    import docx
    d = docx.Document()
    d.core_properties.title = "The Flooded House"
    d.core_properties.author = "Jane Example"
    d.add_heading("Chapter One", level=1)
    for p in CH1:
        d.add_paragraph(p)
    d.add_heading("Chapter Two", level=1)
    for p in CH2:
        d.add_paragraph(p)
    d.save(str(path))
    return path


def make_rtf(path: Path) -> Path:
    body = "\\par\n".join(["Chapter 1"] + CH1 + ["Chapter 2"] + CH2)
    body = body.encode("ascii", "backslashreplace").decode().replace("\\u201c", "\\'93").replace(
        "\\u201d", "\\'94").replace("\\u2019", "\\'92")
    path.write_text("{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Times;}}\n" + body + "\\par\n}", encoding="ascii")
    return path


def make_fb2(path: Path) -> Path:
    def sec(title, paras):
        return f"<section><title><p>{title}</p></title>" + "".join(f"<p>{p}</p>" for p in paras) + "</section>"
    xml = ('<?xml version="1.0" encoding="utf-8"?>'
           '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">'
           "<description><title-info><author><first-name>Jane</first-name><last-name>Example</last-name></author>"
           "<book-title>The Flooded House</book-title></title-info></description>"
           "<body>" + sec("Chapter One", CH1) + sec("Chapter Two", CH2) + "</body>"
           '<body name="notes"><section><p>Note text</p></section></body></FictionBook>')
    path.write_text(xml, encoding="utf-8")
    return path


def make_pdf(path: Path) -> Path:
    import fitz
    doc = fitz.open()
    for title, paras in (("Chapter One", CH1), ("Chapter Two", CH2)):
        page = doc.new_page()
        y = 72
        page.insert_text((72, y), title, fontsize=18)
        y += 40
        for p in paras:
            p = p.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
            rect = fitz.Rect(72, y, 520, y + 80)
            page.insert_textbox(rect, p, fontsize=11)
            y += 90
    doc.set_toc([[1, "Chapter One", 1], [1, "Chapter Two", 2]])
    doc.set_metadata({"title": "The Flooded House", "author": "Jane Example"})
    doc.save(str(path))
    return path


def make_cbz(path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    with zipfile.ZipFile(path, "w") as z:
        for i, lines in enumerate([["WHERE ARE", "YOU GOING?"], ["HOME. IT'S", "LATE."]]):
            img = Image.new("RGB", (800, 1200), "white")
            dr = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arialbd.ttf", 48)
            except OSError:
                font = ImageFont.load_default()
            dr.ellipse((100, 100, 600, 400), outline="black", width=4)
            for k, ln in enumerate(lines):
                dr.text((170, 190 + k * 60), ln, fill="black", font=font)
            buf = io.BytesIO()
            img.save(buf, "JPEG")
            z.writestr(f"page{i + 1:02d}.jpg", buf.getvalue())
        z.writestr("ComicInfo.xml", "<ComicInfo><Series>Night Walk</Series><Number>1</Number>"
                                    "<Writer>Jane Example</Writer></ComicInfo>")
    return path


def _png(w: int, h: int, rgb) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), rgb).save(buf, "PNG")
    return buf.getvalue()


ALL = {"epub": make_epub, "txt": make_txt, "docx": make_docx, "rtf": make_rtf,
       "fb2": make_fb2, "pdf": make_pdf, "cbz": make_cbz}


def build_all(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return {fmt: fn(folder / f"Jane Example - The Flooded House.{fmt}") for fmt, fn in ALL.items()}
