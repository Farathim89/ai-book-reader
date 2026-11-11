"""EPUB 2/3 reader: metadata, cover, TOC-based chapters, paragraphs."""
from __future__ import annotations

import posixpath
import zipfile
from urllib.parse import unquote

from lxml import etree

from .htmltext import extract_blocks
from .model import Book, Chapter, Paragraph
from .textproc import clean_ws

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}
_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)


class EpubError(Exception):
    pass


def _rebuild_opf(raw: bytes):
    """A package file whose metadata is broken XML loses its manifest and spine when parsed in
    recover mode. Rebuild it from the manifest and spine sections plus title/author/cover."""
    import re
    text = raw.decode("utf-8", "replace")

    def section(tag: str) -> str:
        m = re.search(rf"<(?:opf:)?{tag}\b[^>]*>.*?</(?:opf:)?{tag}>", text, re.S | re.I)
        return m.group(0) if m else ""
    dc = "".join(m.group(0) for m in re.finditer(r"<dc:(title|creator)\b[^>]*>.*?</dc:\1>", text, re.S))
    cover = "".join(re.findall(r"<meta\s+name=\"cover\"\s+content=\"[^\"]*\"\s*/>", text))
    synthetic = (f'<package xmlns="{NS["opf"]}" xmlns:opf="{NS["opf"]}" xmlns:dc="{NS["dc"]}">'
                 f"<metadata>{dc}{cover}</metadata>{section('manifest')}{section('spine')}</package>")
    root = etree.fromstring(synthetic.encode("utf-8"), _PARSER)
    if root is None or root.find(".//opf:manifest/opf:item", NS) is None:
        raise EpubError("The EPUB's package file is damaged (no list of contents).")
    return root


class Epub:
    def __init__(self, path: str):
        self.path = path
        try:
            self.z = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise EpubError(f"Not a valid EPUB (zip) file: {e}") from e
        self._names = {n.lower(): n for n in self.z.namelist()}
        if "meta-inf/encryption.xml" in self._names and self._is_drm():
            raise EpubError("This EPUB is DRM-protected. Only DRM-free books can be opened.")
        try:
            container = etree.fromstring(self.read("META-INF/container.xml"), _PARSER)
            rootfile = container.find(".//c:rootfile", NS)
            self.opf_path = rootfile.get("full-path")
        except Exception:
            opfs = [n for n in self.z.namelist() if n.lower().endswith(".opf")]
            if not opfs:
                raise EpubError("EPUB has no package (.opf) file")
            self.opf_path = opfs[0]
        self.base = posixpath.dirname(self.opf_path)
        raw_opf = self.read(self.opf_path)
        self.opf = etree.fromstring(raw_opf, _PARSER)
        if self.opf is None or self.opf.find(".//opf:manifest/opf:item", NS) is None:
            self.opf = _rebuild_opf(raw_opf)
        self.manifest: dict[str, tuple[str, str, str]] = {}
        for it in self.opf.iterfind(".//opf:manifest/opf:item", NS):
            href = self._join(it.get("href", ""))
            self.manifest[it.get("id")] = (href, it.get("media-type", ""), it.get("properties", "") or "")
        spine = self.opf.find(".//opf:spine", NS)
        self.toc_id = spine.get("toc") if spine is not None else None
        self.spine: list[str] = []
        if spine is not None:
            for ref in spine.iterfind("opf:itemref", NS):
                idref = ref.get("idref")
                if idref in self.manifest and ref.get("linear", "yes") != "no":
                    self.spine.append(self.manifest[idref][0])

    # ---- low level ---------------------------------------------------------
    def _join(self, href: str) -> str:
        href = unquote(href.split("#")[0])
        return posixpath.normpath(posixpath.join(self.base, href)) if self.base else posixpath.normpath(href)

    def read(self, name: str) -> bytes:
        real = self._names.get(name.lower()) or self._names.get(unquote(name).lower())
        if real is None:
            raise KeyError(name)
        return self.z.read(real)

    def _is_drm(self) -> bool:
        try:
            enc = self.read("META-INF/encryption.xml").decode("utf-8", "ignore")
        except KeyError:
            return False
        # Many DRM-free EPUBs obfuscate their embedded fonts (IDPF / Adobe). That is harmless;
        # only encrypted text/content files mean real DRM.
        import re
        uris = re.findall(r'CipherReference[^>]*URI="([^"]+)"', enc)
        font_ext = (".ttf", ".otf", ".woff", ".woff2", ".eot", ".ttc")
        return any(not unquote(u).lower().endswith(font_ext) for u in uris)

    # ---- metadata ----------------------------------------------------------
    def title(self) -> str:
        el = self.opf.find(".//dc:title", NS)
        return clean_ws(el.text or "") if el is not None and el.text else ""

    def author(self) -> str:
        names = [clean_ws(e.text) for e in self.opf.iterfind(".//dc:creator", NS) if e.text]
        return ", ".join(dict.fromkeys(names))

    def cover(self) -> bytes | None:
        # EPUB3 cover-image property
        for href, mt, props in self.manifest.values():
            if "cover-image" in props.split():
                return self._try(href)
        # EPUB2 <meta name="cover" content="id">
        for meta in self.opf.iterfind(".//opf:metadata/opf:meta", NS):
            if meta.get("name") == "cover":
                cid = meta.get("content")
                if cid in self.manifest:
                    return self._try(self.manifest[cid][0])
        for iid, (href, mt, _) in self.manifest.items():
            if mt.startswith("image/") and "cover" in (iid + href).lower():
                return self._try(href)
        # first image of the first spine document
        if self.spine:
            try:
                html = self.read(self.spine[0]).decode("utf-8", "ignore")
            except KeyError:
                return None
            import re
            m = re.search(r'(?:src|xlink:href|href)="([^"]+\.(?:jpe?g|png|gif|webp))"', html, re.I)
            if m:
                doc_dir = posixpath.dirname(self.spine[0])
                return self._try(posixpath.normpath(posixpath.join(doc_dir, unquote(m.group(1)))))
        return None

    def _try(self, name: str) -> bytes | None:
        try:
            return self.read(name)
        except KeyError:
            return None

    # ---- table of contents -------------------------------------------------
    def toc(self) -> list[tuple[str, str, str]]:
        """Flat list of (file, fragment, title) in reading order."""
        entries: list[tuple[str, str, str]] = []
        nav = next((h for h, mt, p in self.manifest.values() if "nav" in p.split()), None)
        if nav:
            try:
                entries = self._nav_toc(nav)
            except Exception:
                entries = []
        if not entries:
            ncx = self.manifest.get(self.toc_id or "", (None,))[0] if self.toc_id else None
            if not ncx:
                ncx = next((h for h, mt, p in self.manifest.values() if mt == "application/x-dtbncx+xml"), None)
            if ncx:
                try:
                    entries = self._ncx_toc(ncx)
                except Exception:
                    entries = []
        return entries

    def _split(self, doc_path: str, href: str) -> tuple[str, str]:
        file_part, _, frag = href.partition("#")
        doc_dir = posixpath.dirname(doc_path)
        full = posixpath.normpath(posixpath.join(doc_dir, unquote(file_part))) if file_part else doc_path
        return full, unquote(frag)

    def _nav_toc(self, nav_path: str) -> list[tuple[str, str, str]]:
        root = etree.fromstring(self.read(nav_path), _PARSER)
        navs = root.findall(".//x:nav", NS) or root.findall(".//nav")
        toc_nav = None
        for n in navs:
            if n.get(f"{{{NS['epub']}}}type") == "toc" or n.get("type") == "toc":
                toc_nav = n
                break
        toc_nav = toc_nav if toc_nav is not None else (navs[0] if navs else None)
        if toc_nav is None:
            return []
        out = []
        for a in toc_nav.iter(f"{{{NS['x']}}}a", "a"):
            href = a.get("href")
            if not href:
                continue
            title = clean_ws("".join(a.itertext()))
            f, frag = self._split(nav_path, href)
            out.append((f, frag, title))
        return out

    def _ncx_toc(self, ncx_path: str) -> list[tuple[str, str, str]]:
        root = etree.fromstring(self.read(ncx_path), _PARSER)
        out = []
        for np_ in root.iter(f"{{{NS['ncx']}}}navPoint"):
            label = np_.find("ncx:navLabel/ncx:text", NS)
            content = np_.find("ncx:content", NS)
            if content is None:
                continue
            f, frag = self._split(ncx_path, content.get("src", ""))
            out.append((f, frag, clean_ws(label.text or "") if label is not None and label.text else ""))
        return out


def load_epub(path: str, fmt: str = "epub") -> Book:
    ep = Epub(path)
    toc = ep.toc()
    file_titles: dict[str, str] = {}
    frag_titles: dict[str, dict[str, str]] = {}
    for f, frag, title in toc:
        if frag:
            frag_titles.setdefault(f, {}).setdefault(frag, title)
        else:
            file_titles.setdefault(f, title)

    chapters: list[Chapter] = []
    cur: Chapter | None = None
    for doc in ep.spine:
        try:
            data = ep.read(doc)
        except KeyError:
            continue
        frags = frag_titles.get(doc, {})
        blocks = extract_blocks(data, set(frags))
        if not blocks:
            continue
        if doc in file_titles:
            cur = Chapter(file_titles[doc] or "")
            chapters.append(cur)
        elif cur is None:
            cur = Chapter("")
            chapters.append(cur)
        for b in blocks:
            if b.marker:
                cur = Chapter(frags.get(b.marker, ""))
                chapters.append(cur)
            cur.paragraphs.append(Paragraph(text=b.text, kind=b.kind))

    for i, ch in enumerate(chapters):
        if not ch.title:
            head = next((p.text for p in ch.paragraphs[:3] if p.kind == "h"), None)
            ch.title = (head or (ch.paragraphs[0].text if ch.paragraphs else ""))[:70] or f"Section {i + 1}"
    from .chapters import refine_chapters
    chapters = refine_chapters([c for c in chapters if c.paragraphs])
    book = Book(path=path, fmt=fmt, title=ep.title() or "", author=ep.author(), chapters=chapters)
    book.cover = ep.cover()
    return book


def epub_meta(path: str) -> tuple[str, str, bytes | None]:
    ep = Epub(path)
    return ep.title(), ep.author(), ep.cover()
