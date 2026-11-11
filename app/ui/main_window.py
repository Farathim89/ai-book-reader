"""Main window: library page + reader page, wiring narration, jobs and dialogs."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFileDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..ai.ollama import Ollama, OllamaError
from ..audio.export import ExportJob
from ..audio.narrator import Narrator
from ..audio.player import AudioPlayer
from ..formats import FILE_FILTER, BookError
from ..library import store
from ..library.db import Library, scan_folder
from ..paths import resource_dir
from ..tts import voices as V
from ..tts.planner import VoicePlanner, auto_cast
from . import theme
from .comic_view import ComicReader
from .dialogs import CastDialog, ExportDialog, SettingsDialog, VoicesDialog
from .jobs import Job, JobTray
from .library_view import LibraryView
from .player_bar import PlayerBar
from .reader_view import TextReader
from .widgets import Bar, IconButton, muted, vline


class MainWindow(QMainWindow):
    def __init__(self, settings, library: Library, tts):
        super().__init__()
        self.settings = settings
        self.library = library
        self.tts = tts
        self.narrator = Narrator(tts, self)
        self.preview_player = AudioPlayer(tts.sample_rate)
        self.book = None
        self.book_id: int | None = None
        self._last_save = 0.0
        self._last_apply = 0.0
        self._pending_pos: int | None = None
        self.setWindowTitle(APP_NAME)
        icon = resource_dir() / "assets" / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1280, 860)
        self.setMinimumSize(900, 600)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._build_library_page())
        self.stack.addWidget(self._build_reader_page())

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_label = muted("")
        sb.addWidget(self.status_label, 1)
        self.jobs = JobTray()
        sb.addPermanentWidget(self.jobs)

        n = self.narrator
        n.unitStarted.connect(self._on_unit)
        n.stateChanged.connect(self._on_state)
        n.status.connect(self._status)
        n.error.connect(lambda msg: QMessageBox.warning(self, "Narration", msg))
        n.set_volume(float(settings["volume"]))
        n.set_speed(float(settings["speed"]))
        self._shortcuts()

        geo = settings["window_geometry"]
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        self.library_view.reload()
        QTimer.singleShot(400, self._rescan_folders)
        QTimer.singleShot(1500, self._warm_up)

    def _warm_up(self) -> None:
        """Load the default narrator's voice model in the background, so Play starts quickly."""
        import threading
        vid = self.tts.resolve(self.settings["narrator_voice"])
        if not self.tts.is_loaded(vid):
            threading.Thread(target=lambda: self._quiet(self.tts.ensure_loaded, [vid]), daemon=True).start()

    @staticmethod
    def _quiet(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - surfaced later when the user presses Play
            pass

    # =========================================================================== layout
    def _build_library_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = Bar("TopBar", margins=(18, 10, 14, 10))
        logo = QLabel(theme.I["headphones"])
        theme.use_icon_font(logo, 20, f"color: {theme.ACCENT};")
        top.add(logo)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        top.add(title)
        top.lay.addSpacing(18)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title or author")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        top.add(self.search)
        self.sort = QComboBox()
        for label, key in (("Recently read", "recent"), ("Title", "title"), ("Author", "author"),
                           ("Recently added", "added")):
            self.sort.addItem(label, key)
        self.sort.setCurrentIndex(max(0, self.sort.findData(self.settings["library_sort"])))
        top.add(self.sort)
        top.stretch()
        b_books = top.add(IconButton("add", "Add book files", 14, text="Add books"))
        b_folder = top.add(IconButton("folder", "Add a folder of books (it is re-scanned on start)", 14,
                                      text="Add folder"))
        top.add(vline())
        b_voices = top.add(IconButton("mic", "Voices: preview, default narrator, voice cloning", 14, text="Voices"))
        b_settings = top.add(IconButton("settings", "Settings", 15))
        lay.addWidget(top)
        self.library_view = LibraryView(self.library)
        self.library_view.model.sort = self.settings["library_sort"]
        lay.addWidget(self.library_view, 1)

        self.search.textChanged.connect(self.library_view.set_filter)
        self.sort.currentIndexChanged.connect(self._sort_changed)
        b_books.clicked.connect(self.add_books)
        b_folder.clicked.connect(self.add_folder)
        b_voices.clicked.connect(self.show_voices)
        b_settings.clicked.connect(self.show_settings)
        lv = self.library_view
        lv.openBook.connect(self.open_book)
        lv.exportBook.connect(self.export_book)
        lv.removeBook.connect(self.remove_book)
        lv.revealBook.connect(self.reveal_book)
        lv.addFolder.connect(self.add_folder)
        lv.addBooks.connect(self.add_books)
        return page

    def _build_reader_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = Bar("TopBar", margins=(10, 8, 12, 8))
        back = top.add(IconButton("back", "Back to library (Esc)", 15))
        back.clicked.connect(self.close_book)
        top.lay.addSpacing(6)
        self.book_title = QLabel("")
        self.book_title.setObjectName("BookTitle")
        top.add(self.book_title)
        self.book_author = muted("")
        top.add(self.book_author)
        top.stretch()
        self.chapter_box = QComboBox()
        self.chapter_box.setMinimumWidth(240)
        self.chapter_box.setMaximumWidth(380)
        self.chapter_box.setToolTip("Chapters")
        self.chapter_box.activated.connect(self._chapter_chosen)
        top.add(self.chapter_box)
        # comic page navigation
        self.page_prev = top.add(IconButton("left", "Previous page (Page Up)", 13))
        self.page_label = top.add(muted(""))
        self.page_next = top.add(IconButton("right", "Next page (Page Down)", 13))
        self.page_prev.clicked.connect(lambda: self._page_step(-1))
        self.page_next.clicked.connect(lambda: self._page_step(1))
        top.add(vline())
        self.font_down = top.add(self._text_button("A−", "Smaller text (Ctrl -)", 11))
        self.font_up = top.add(self._text_button("A+", "Bigger text (Ctrl +)", 14))
        self.font_down.clicked.connect(lambda: self._font_step(-1))
        self.font_up.clicked.connect(lambda: self._font_step(1))
        # characters menu
        self.char_btn = IconButton("people", "Character voices", 14, text="Characters")
        self.char_btn.setPopupMode(QToolButton.InstantPopup)
        self.char_menu = QMenu(self)
        self.char_btn.setMenu(self.char_menu)
        self.char_menu.aboutToShow.connect(self._fill_char_menu)
        top.add(self.char_btn)
        # comic / scan menu
        self.ocr_btn = IconButton("sparkle", "Comic and scanned-page reading", 14, text="Pages")
        self.ocr_btn.setPopupMode(QToolButton.InstantPopup)
        self.ocr_menu = QMenu(self)
        self.ocr_btn.setMenu(self.ocr_menu)
        self.ocr_menu.aboutToShow.connect(self._fill_ocr_menu)
        top.add(self.ocr_btn)
        exp = top.add(IconButton("save", "Make an M4B audiobook of this book", 14, text="Audiobook"))
        exp.clicked.connect(lambda: self.export_book(self.book_id))
        lay.addWidget(top)

        self.reader_stack = QStackedWidget()
        self.text_reader = TextReader()
        self.comic_reader = ComicReader()
        self.reader_stack.addWidget(self.text_reader)
        self.reader_stack.addWidget(self.comic_reader)
        lay.addWidget(self.reader_stack, 1)
        for r in (self.text_reader, self.comic_reader):
            r.unitClicked.connect(self.narrator.jump)
            r.unitActivated.connect(lambda u: self.narrator.play(u))
        self.comic_reader.pageChanged.connect(self._on_page)

        self.player_bar = PlayerBar()
        pb = self.player_bar
        pb.playClicked.connect(self._toggle)
        pb.stepClicked.connect(self.narrator.step)
        pb.chapterClicked.connect(self.narrator.chapter_step)
        pb.seek.connect(self.narrator.jump)
        pb.voiceChanged.connect(self._voice_changed)
        pb.speedChanged.connect(self._speed_changed)
        pb.volumeChanged.connect(self._volume_changed)
        lay.addWidget(pb)
        return page

    @staticmethod
    def _text_button(text: str, tip: str, pt: int) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f'font-family: "Georgia"; font-size: {pt}pt; padding: 2px 8px;')
        return b

    def _shortcuts(self) -> None:
        def sc(keys, fn):
            for k in (keys if isinstance(keys, (list, tuple)) else [keys]):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.WindowShortcut)
                s.activated.connect(lambda fn=fn: self._in_reader() and fn())
        sc("Space", self._toggle)
        sc("Left", lambda: self.narrator.step(-1))
        sc("Right", lambda: self.narrator.step(1))
        sc("Ctrl+Left", lambda: self.narrator.chapter_step(-1))
        sc("Ctrl+Right", lambda: self.narrator.chapter_step(1))
        sc(["Ctrl+=", "Ctrl++"], lambda: self._font_step(1))
        sc("Ctrl+-", lambda: self._font_step(-1))
        sc("Escape", self.close_book)
        sc("PgDown", lambda: self._page_step(1))
        sc("PgUp", lambda: self._page_step(-1))
        f = QShortcut(QKeySequence("Ctrl+F"), self)
        f.activated.connect(lambda: (not self._in_reader()) and self.search.setFocus())

    def _in_reader(self) -> bool:
        return self.stack.currentIndex() == 1 and self.book is not None

    def _status(self, msg: str) -> None:
        self.status_label.setText(msg)

    # =========================================================================== library
    def _sort_changed(self) -> None:
        key = self.sort.currentData()
        self.settings["library_sort"] = key
        self.library_view.set_sort(key)

    def add_books(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add books", "", FILE_FILTER + ";;All files (*)")
        if files:
            self._import(files, "Adding books")

    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add a folder of books")
        if not d:
            return
        folders = list(self.settings["library_folders"])
        if d not in folders:
            folders.append(d)
            self.settings["library_folders"] = folders
            self.settings.save()
        self._import(None, "Scanning folder", [d])

    def _rescan_folders(self) -> None:
        folders = [f for f in self.settings["library_folders"] if os.path.isdir(f)]
        if folders:
            self._import(None, "Updating library", folders, quiet=True)

    def _import(self, files, title: str, folders=None, quiet: bool = False) -> None:
        if self.jobs.busy(title):
            return

        def run(progress, cancelled):
            paths = list(files or [])
            for folder in folders or []:
                paths += list(scan_folder(folder))
            added = 0
            for k, p in enumerate(paths):
                if cancelled():
                    break
                if self.library.by_path(p):
                    continue
                try:
                    if self.library.add(p):
                        added += 1
                except Exception:  # noqa: BLE001 - skip unreadable files
                    pass
                progress((k + 1) / max(1, len(paths)), f"{added} new")
            return added

        job = Job(title, run)
        job.done.connect(lambda n: (self.library_view.reload(),
                                    self._status(f"Added {n} book{'s' if n != 1 else ''}." if n or not quiet else "")))
        job.failed.connect(lambda e: QMessageBox.warning(self, title, e))
        job.progress.connect(lambda f, m: self._maybe_reload_library())
        self.jobs.run(job)

    def _maybe_reload_library(self) -> None:
        if time.time() - getattr(self, "_lib_reload", 0) > 1.5 and self.stack.currentIndex() == 0:
            self._lib_reload = time.time()
            self.library_view.reload()

    def remove_book(self, book_id: int) -> None:
        r = self.library.get(book_id)
        if not r:
            return
        if QMessageBox.question(self, "Remove book", f"Remove “{r['title']}” from the library?\n\n"
                                "The book file itself is not deleted.") == QMessageBox.Yes:
            self.library.remove(book_id)
            self.library_view.model.drop_cover(book_id)
            self.library_view.reload()

    def reveal_book(self, book_id: int) -> None:
        r = self.library.get(book_id)
        if r and os.path.exists(r["path"]):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(r["path"])])

    # =========================================================================== open / close
    def open_book(self, book_id: int) -> None:
        r = self.library.get(book_id)
        if not r:
            return
        if not os.path.exists(r["path"]):
            QMessageBox.warning(self, "Open book", f"The file was not found:\n{r['path']}")
            return
        if self.book_id == book_id and self.book is not None:
            self.stack.setCurrentIndex(1)
            return
        self._status(f"Opening “{r['title']}”…")
        job = Job("Opening book", lambda progress, cancelled: store.open_book(book_id, r["path"]), cancellable=False)
        job.done.connect(lambda book: self._book_loaded(book_id, book))
        job.failed.connect(lambda e: (self._status(""), QMessageBox.warning(self, "Open book", e)))
        self.jobs.run(job)

    def _book_loaded(self, book_id: int, book, keep_position: int | None = None, show: bool = True) -> None:
        self._save_position()
        self.narrator.stop()
        self.book, self.book_id = book, book_id
        r = self.library.get(book_id)
        self.library.update(book_id, opened=time.time(), total=book.unit_count(),
                            title=r["title"] if r["title"] else book.title)
        pos = keep_position if keep_position is not None else int(r["position"] or 0)
        pos = max(0, min(pos, book.unit_count() - 1)) if book.unit_count() else 0
        self.narrator.set_book(book, self._make_planner(), pos)
        self.book_title.setText(r["title"] or book.title)
        self.book_author.setText(r["author"] or book.author)
        self.setWindowTitle(f"{r['title'] or book.title} — {APP_NAME}")
        self.chapter_box.clear()
        for ci, ch in enumerate(book.chapters):
            self.chapter_box.addItem(f"{ci + 1}. {ch.title}"[:90])
        comic = book.kind == "comic"
        for w in (self.page_prev, self.page_label, self.page_next):
            w.setVisible(comic)
        for w in (self.font_up, self.font_down):
            w.setVisible(not comic)
        self.ocr_btn.setVisible(comic or bool(book.ocr_pages_needed) or bool(store.load_ocr(book_id)))
        self.char_btn.setVisible(not comic)
        self.chapter_box.setVisible(len(book.chapters) > 1 or not comic)
        self.player_bar.set_voices(V.all_voices(), self.narrator.planner.narrator)
        self.player_bar.set_speed(self.narrator.speed)
        self.player_bar.set_volume(self.narrator.player.volume)
        if comic:
            from ..formats.cbz import read_page
            path = book.path
            self.reader_stack.setCurrentWidget(self.comic_reader)
            self.comic_reader.set_book(book, lambda i: read_page(path, book.pages[i]))
            if book.unit_count():
                self.comic_reader.highlight(pos)
            else:
                self.comic_reader.show_page(0)
        else:
            self.reader_stack.setCurrentWidget(self.text_reader)
            self.text_reader.set_font(self.settings["reader_font"], int(self.settings["font_size"]))
            self.text_reader.book = None
            if book.chapters:
                self.text_reader.show_chapter(book, book.chapter_of(pos))
                self.text_reader.highlight(pos, center=True)
            else:
                self.text_reader.setPlainText("")
        self._update_position(pos)
        if show:
            self.stack.setCurrentIndex(1)
        self._status("")
        self.narrator.preload()
        if book.ocr_pages_needed and show:
            QTimer.singleShot(200, self._offer_ocr)

    def close_book(self) -> None:
        if self.stack.currentIndex() != 1:
            return
        self.narrator.pause()
        self._save_position()
        self.stack.setCurrentIndex(0)
        self.setWindowTitle(APP_NAME)
        self.library_view.reload()

    def _book_settings(self) -> dict:
        r = self.library.get(self.book_id) if self.book_id else None
        return r["settings"] if r else {}

    def _make_planner(self, book=None, book_id=None) -> VoicePlanner:
        book = book or self.book
        book_id = book_id or self.book_id
        r = self.library.get(book_id)
        bs = r["settings"] if r else {}
        chars = store.load_characters(book_id)
        narrator = self.tts.resolve(bs.get("narrator") or self.settings["narrator_voice"])
        cast = {k: self.tts.resolve(v) for k, v in (bs.get("cast") or {}).items()}
        use = bool(bs.get("use_characters")) and bool(chars["characters"])
        s = self.settings
        pauses = {"sentence": float(s["pause_sentence"]), "paragraph": float(s["pause_paragraph"]),
                  "heading": float(s["pause_heading"]), "chapter": float(s["pause_chapter"])}
        return VoicePlanner(book, narrator, cast, use, pauses)

    # =========================================================================== narration UI
    def _toggle(self) -> None:
        if self.book and self.book.unit_count() == 0:
            if self.book.ocr_pages_needed:
                self._offer_ocr()
            return
        self.narrator.toggle()

    def _on_state(self, state: str) -> None:
        self.player_bar.set_state(state)
        if state == "loading":
            self._status("Generating speech…" if self.tts.is_loaded(self.narrator.planner.narrator)
                         else "Loading voice model…")
        elif state in ("playing", "paused", "stopped") and self.status_label.text().startswith(("Generating", "Loading")):
            self._status("")

    def _on_unit(self, u: int) -> None:
        if not self.book:
            return
        if self.book.kind == "comic":
            self.comic_reader.highlight(u)
        else:
            ci = self.book.chapter_of(u)
            if self.text_reader.ci != ci or self.text_reader.book is not self.book:
                self.text_reader.show_chapter(self.book, ci)
                self.text_reader.highlight(u, center=True)
            else:
                self.text_reader.highlight(u)
        self._update_position(u)
        if time.time() - self._last_save > 4:
            self._save_position()

    def _update_position(self, u: int) -> None:
        if not self.book:
            return
        n = self.book.unit_count()
        ci = self.book.chapter_of(u) if n else 0
        self.chapter_box.setCurrentIndex(ci)
        self.player_bar.set_position(u, n, ci, max(1, len(self.book.chapters)))

    def _save_position(self) -> None:
        if self.book_id is not None and self.book is not None and self.book.unit_count():
            self.library.update(self.book_id, position=int(self.narrator.current), total=self.book.unit_count())
            self._last_save = time.time()

    def _chapter_chosen(self, ci: int) -> None:
        if self.book and 0 <= ci < len(self.book.chapters):
            self.narrator.jump(self.book.chapter_first_unit[ci])

    def _page_step(self, d: int) -> None:
        if self.book and self.book.kind == "comic":
            self.comic_reader.show_page(self.comic_reader.page + d)

    def _on_page(self, page: int, count: int) -> None:
        self.page_label.setText(f"Page {page + 1} / {count}")

    def _font_step(self, d: int) -> None:
        if not self.book or self.book.kind == "comic":
            return
        size = max(10, min(36, int(self.settings["font_size"]) + d))
        self.settings["font_size"] = size
        self.text_reader.set_font(self.settings["reader_font"], size)

    def _voice_changed(self, voice_id: str) -> None:
        if not voice_id or not self.book_id:
            return
        self.library.update_settings(self.book_id, narrator=voice_id)
        self.narrator.set_planner(self._make_planner())
        if not self.tts.is_loaded(voice_id):
            self.narrator.preload()

    def _speed_changed(self, speed: float) -> None:
        self.settings["speed"] = speed
        self.narrator.set_speed(speed)

    def _volume_changed(self, v: float) -> None:
        self.settings["volume"] = v
        self.narrator.set_volume(v)

    # =========================================================================== characters
    def _fill_char_menu(self) -> None:
        m = self.char_menu
        m.clear()
        if not self.book:
            return
        chars = store.load_characters(self.book_id)
        bs = self._book_settings()
        has = bool(chars["characters"])
        quotes = self.book.quote_count()
        use = QAction("Speak dialogue with character voices", m, checkable=True)
        use.setChecked(bool(bs.get("use_characters")) and has)
        use.setEnabled(has)
        use.toggled.connect(self._toggle_characters)
        m.addAction(use)
        m.addSeparator()
        done = set(chars["done_chapters"])
        todo = [ci for ci, ch in enumerate(self.book.chapters) if ci not in done and any(p.quotes for p in ch.paragraphs)]
        if not quotes:
            a = m.addAction("No dialogue found in this book")
            a.setEnabled(False)
        elif self.jobs.busy("Finding characters"):
            a = m.addAction("Finding characters… (running)")
            a.setEnabled(False)
        elif todo and done:
            m.addAction(f"Continue finding characters ({len(todo)} chapters left)", self.find_characters)
        elif todo:
            m.addAction("Find characters with AI…", self.find_characters)
        else:
            m.addAction("Find characters again (start over)…", lambda: self.find_characters(restart=True))
        e = m.addAction(f"Edit character voices… ({len(chars['characters'])})", self.edit_cast)
        e.setEnabled(has)

    def _toggle_characters(self, on: bool) -> None:
        self.library.update_settings(self.book_id, use_characters=on)
        self.narrator.set_planner(self._make_planner())
        if on:
            self.narrator.preload()

    def find_characters(self, restart: bool = False) -> None:
        if not self.book:
            return
        ollama = Ollama(self.settings["ollama_url"])
        model = self.settings["text_model"]
        try:
            if not ollama.has_model(model):
                QMessageBox.information(self, "Find characters",
                                        f"The model “{model}” is not installed in Ollama.\n\n"
                                        f"Install it with:  ollama pull {model}\nor pick another model in Settings.")
                return
        except OllamaError as e:
            QMessageBox.information(self, "Find characters", str(e))
            return
        book, book_id = self.book, self.book_id
        if restart:
            store.save_characters(book_id, {})
        data = store.load_characters(book_id)
        n_quotes = book.quote_count()
        if not data["done_chapters"] and n_quotes > 400:
            mins = max(1, int(n_quotes / 45 * 12 / 60))
            if QMessageBox.question(self, "Find characters",
                                    f"This book has {n_quotes:,} lines of dialogue. The local AI needs roughly "
                                    f"{mins} minutes to work out who says what (it starts with the current "
                                    "chapter, and you can keep reading meanwhile).\n\nStart now?") != QMessageBox.Yes:
                return
        start = book.chapter_of(self.narrator.current) if book.units else 0

        def run(progress, cancelled):
            from ..ai.characters import analyze_book
            try:
                return analyze_book(book, data, ollama, model, progress=progress, cancelled=cancelled,
                                    save=lambda d: store.save_characters(book_id, d), start_chapter=start)
            finally:
                ollama.unload(model)

        job = Job("Finding characters", run)
        job.progress.connect(lambda f, m: self._apply_characters(book_id, live=True))
        job.done.connect(lambda d: self._characters_done(book_id))
        job.failed.connect(lambda e: QMessageBox.warning(self, "Find characters", e))
        self.library.update_settings(book_id, use_characters=True)
        self.jobs.run(job)

    def _apply_characters(self, book_id: int, live: bool = False, final: bool = False) -> None:
        if book_id != self.book_id or not self.book:
            return
        if live and time.time() - self._last_apply < 4:
            return
        self._last_apply = time.time()
        data = store.load_characters(book_id)
        store.apply_speakers(self.book, data)
        bs = self._book_settings()
        keep = bs.get("cast") or {}
        if final:   # genders are known now: re-assign every voice the user did not pick by hand
            manual = set(bs.get("cast_manual") or [])
            keep = {k: v for k, v in keep.items() if k in manual}
        cast = auto_cast(data["characters"], self.narrator.planner.narrator, keep)
        if cast != (bs.get("cast") or {}):
            self.library.update_settings(book_id, cast=cast)
        planner = self.narrator.planner
        if live and planner is not None:
            planner.cast.update({k: self.tts.resolve(v) for k, v in cast.items()})
            planner.use_characters = bool(data["characters"]) and bool(self._book_settings().get("use_characters"))
        else:
            self.narrator.set_planner(self._make_planner())

    def _characters_done(self, book_id: int) -> None:
        self._apply_characters(book_id, final=True)
        n = len(store.load_characters(book_id)["characters"])
        if book_id == self.book_id:
            self._status(f"Found {n} speaking characters. Characters ▸ Edit character voices to change who sounds like whom.")
            self.narrator.preload()

    def edit_cast(self) -> None:
        if not self.book:
            return
        data = store.load_characters(self.book_id)
        bs = self._book_settings()
        d = CastDialog(data["characters"], bs.get("cast") or {}, self.narrator.planner.narrator,
                       bool(bs.get("use_characters")), self.tts, self.preview_player, self)
        if d.exec():
            old = set() if d.reset_manual else set(bs.get("cast_manual") or [])
            manual = sorted(old | d.changed_names)
            self.library.update_settings(self.book_id, cast=d.result_cast(), use_characters=d.use.isChecked(),
                                         cast_manual=manual)
            self.narrator.set_planner(self._make_planner())
            self.narrator.preload()

    # =========================================================================== OCR
    def _fill_ocr_menu(self) -> None:
        m = self.ocr_menu
        m.clear()
        if not self.book:
            return
        comic = self.book.kind == "comic"
        need = len(self.book.ocr_pages_needed)
        if comic:
            rtl = QAction("Right-to-left pages (manga)", m, checkable=True)
            rtl.setChecked(bool(self._book_settings().get("rtl")))
            rtl.toggled.connect(self._toggle_rtl)
            m.addAction(rtl)
            m.addSeparator()
        if self.jobs.busy("Reading pages"):
            a = m.addAction("Reading pages… (running)")
            a.setEnabled(False)
        elif need:
            m.addAction(f"Read text on {need} page{'s' if need != 1 else ''} (OCR)…", self.run_ocr)
        m.addAction("Read all pages again…", lambda: self.run_ocr(redo=True))
        m.addSeparator()
        vm = self.settings["vision_model"]
        m.addAction(f"Download vision model ({vm})…", self.pull_vision_model)

    def _toggle_rtl(self, on: bool) -> None:
        self.library.update_settings(self.book_id, rtl=on)
        if store.load_ocr(self.book_id) and QMessageBox.question(
                self, "Reading order", "Read the pages again in the new order now?") == QMessageBox.Yes:
            self.run_ocr(redo=True)

    def _vision_ready(self) -> tuple[Ollama | None, bool]:
        ollama = Ollama(self.settings["ollama_url"])
        try:
            return ollama, ollama.has_model(self.settings["vision_model"])
        except OllamaError:
            return None, False

    def _offer_ocr(self) -> None:
        if not self.book or not self.book.ocr_pages_needed or self.jobs.busy("Reading pages"):
            return
        n = len(self.book.ocr_pages_needed)
        comic = self.book.kind == "comic"
        ollama, vision = self._vision_ready()
        what = (f"This comic has {n} page{'s' if n != 1 else ''} whose speech bubbles have not been read yet."
                if comic else f"{n} page{'s' if n != 1 else ''} of this PDF are scanned images without text.")
        box = QMessageBox(self)
        box.setWindowTitle("Read pages")
        box.setIcon(QMessageBox.Information)
        detail = ("The local vision model will read them (about 3 seconds per page, once per book)." if vision else
                  "Without a vision model the basic built-in OCR is used: fast, but it can miss words or get the "
                  "bubble order wrong. For good results download a vision model first (about 6 GB, one time).")
        box.setText(what + "\n\n" + detail)
        read = box.addButton("Read pages now", QMessageBox.AcceptRole)
        dl = None
        if not vision and ollama is not None:
            dl = box.addButton("Download vision model", QMessageBox.ActionRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is read:
            self.run_ocr()
        elif dl is not None and box.clickedButton() is dl:
            self.pull_vision_model(then_ocr=True)

    def run_ocr(self, redo: bool = False) -> None:
        if not self.book or self.jobs.busy("Reading pages"):
            return
        book, book_id = self.book, self.book_id
        comic = book.kind == "comic"
        rtl = bool(self._book_settings().get("rtl"))
        ollama, vision = self._vision_ready()
        model = self.settings["vision_model"]
        ocr = {} if redo else store.load_ocr(book_id)
        if comic:
            pages = list(range(len(book.pages))) if redo else [p for p in book.ocr_pages_needed if p not in ocr]
        else:
            from ..formats.pdf import scanned_pages
            pages = scanned_pages(book.path) if redo else [p for p in book.ocr_pages_needed if p not in ocr]
        if not pages:
            return
        if redo:
            store.save_ocr(book_id, {})

        def run(progress, cancelled):
            from ..ai.ocr import PageReader
            from ..formats.cbz import read_page
            from ..formats.pdf import render_page_png
            reader = PageReader(ollama if vision else None, model if vision else None)
            try:
                for k, p in enumerate(pages):
                    if cancelled():
                        break
                    if comic:
                        ocr[p] = reader.comic_page(read_page(book.path, book.pages[p]), rtl)
                    else:
                        ocr[p] = reader.scan_page(render_page_png(book.path, p))
                    store.save_ocr(book_id, ocr)
                    progress((k + 1) / len(pages), f"Page {k + 1} of {len(pages)}")
            finally:
                if vision and ollama:
                    ollama.unload(model)
            return len(ocr)

        job = Job("Reading pages", run)
        job.ended.connect(lambda: self._ocr_finished(book_id))
        job.failed.connect(lambda e: QMessageBox.warning(self, "Read pages", e))
        self.jobs.run(job)

    def _ocr_finished(self, book_id: int) -> None:
        store.invalidate(book_id)
        if book_id != self.book_id:
            return
        r = self.library.get(book_id)
        pos = self.narrator.current
        try:
            book = store.open_book(book_id, r["path"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Read pages", str(e))
            return
        in_reader = self.stack.currentIndex() == 1
        self._book_loaded(book_id, book, keep_position=pos, show=in_reader)
        if not in_reader:
            self.library_view.reload()

    def pull_vision_model(self, then_ocr: bool = False) -> None:
        model = self.settings["vision_model"]
        ollama = Ollama(self.settings["ollama_url"])
        if not ollama.available():
            QMessageBox.information(self, "Vision model", "Ollama is not running. Start Ollama and try again.")
            return
        if self.jobs.busy(f"Downloading {model}"):
            return
        job = Job(f"Downloading {model}", lambda progress, cancelled: ollama.pull(model, progress, cancelled))
        job.done.connect(lambda _: (self._status(f"{model} is ready."), then_ocr and self.run_ocr()))
        job.failed.connect(lambda e: QMessageBox.warning(self, "Vision model", e))
        self.jobs.run(job)

    # =========================================================================== export
    def export_book(self, book_id: int | None) -> None:
        if not book_id:
            return
        r = self.library.get(book_id)
        if not r:
            return
        title = f"Audiobook: {r['title']}"[:60]
        if self.jobs.busy(title):
            QMessageBox.information(self, "Make audiobook", "This book is already being exported.")
            return
        bs = r["settings"]
        narrator = V.find_voice(self.tts.resolve(bs.get("narrator") or self.settings["narrator_voice"]))
        n_chars = len(store.load_characters(book_id)["characters"])
        chars = (f"character voices on ({n_chars} characters)" if bs.get("use_characters") and n_chars
                 else "one narrator voice (no character voices)")
        summary = f"Narrator: {narrator.name} ({narrator.detail}) · {chars} · speed {float(self.settings['speed']):g}×"
        d = ExportDialog(r["title"], r["author"], self.settings, summary, self)
        if not d.exec():
            return
        out = str(d.out_path())

        def run(progress, cancelled):
            book = self.book if (book_id == self.book_id and self.book is not None) else store.open_book(book_id, r["path"])
            book.title, book.author = r["title"] or book.title, r["author"] or book.author   # library (Calibre) names
            if book.unit_count() == 0:
                raise RuntimeError("This book has no readable text yet. For comics and scanned PDFs, "
                                   "open the book and use Pages ▸ Read text first.")
            planner = self._make_planner(book, book_id)
            cover = book.cover
            if not cover:
                from ..formats import read_meta
                cover = read_meta(r["path"])[2]
            if not cover and r.get("cover") and os.path.exists(r["cover"]):
                cover = Path(r["cover"]).read_bytes()
            exp = ExportJob(book_id, book, planner, self.tts, out, speed=float(self.settings["speed"]),
                            bitrate=self.settings["export_bitrate"], cover=cover, progress=progress)
            job.on_cancel = exp.cancel
            if cancelled():
                exp.cancel()
            return exp.run()

        job = Job(title, run)
        job.done.connect(lambda path: self._export_done(path))
        job.failed.connect(lambda e: QMessageBox.warning(self, "Make audiobook", e))
        self.jobs.run(job)

    def _export_done(self, path) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Audiobook ready")
        box.setText(f"Saved:\n{path}")
        show = box.addButton("Show in folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is show:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(str(path))])

    # =========================================================================== dialogs
    def show_voices(self) -> None:
        d = VoicesDialog(self.tts, self.preview_player, self.settings, self)
        d.exec()
        if d.changed and self.book:
            self.player_bar.set_voices(V.all_voices(), self.narrator.planner.narrator)
            self.narrator.set_planner(self._make_planner())

    def show_settings(self) -> None:
        try:
            models = Ollama(self.settings["ollama_url"]).models()
        except OllamaError:
            models = []
        d = SettingsDialog(self.settings, models, self)
        if d.exec():
            if self.book:
                self.narrator.set_planner(self._make_planner())
                if self.book.kind != "comic":
                    self.text_reader.set_font(self.settings["reader_font"], int(self.settings["font_size"]))

    # =========================================================================== shutdown
    def closeEvent(self, e) -> None:
        self._save_position()
        self.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        self.jobs.cancel_all()
        self.narrator.shutdown()
        self.preview_player.close()
        super().closeEvent(e)
