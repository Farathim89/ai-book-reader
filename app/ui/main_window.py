"""Main window in Audiobookshelf style: app bar, left sidebar, pages, and a bottom player."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..ai.ollama import Ollama, OllamaError
from ..audio.export import ExportJob
from ..audio.narrator import Narrator
from ..audio.player import AudioPlayer
from ..formats import FILE_FILTER
from ..library import libraries as L
from ..library import store, views
from ..library.db import Library, scan_folder
from ..paths import resource_dir
from ..tts import voices as V
from ..tts.planner import VoicePlanner, auto_cast
from . import theme
from .comic_view import ComicReader
from .. import updater
from .dialogs import CastDialog, ExportDialog, SettingsDialog, UpdateDialog, VoicesDialog
from .jobs import Job, JobManager, JobsButton, JobsPanel
from .pages import BookPage, HomePage, ShelfPage, book_item
from .player_bar import PlayerBar
from .reader_view import TextReader
from .shelf import ShelfItem
from .widgets import Bar, IconButton, glyph_icon, muted, vline

NAV = [("home", "home", "Home"), ("library", "library", "Library"), ("series", "series", "Series"),
       ("authors", "author", "Authors")]
NAV_OF_PAGE = {"home": "home", "library": "library", "series": "series", "series_books": "series",
               "authors": "authors", "author_books": "authors"}


class _Loader(QObject):
    """Parses books on a thread and hands them back on the UI thread."""
    loaded = Signal(int, object, str)    # book id, Book or None, error
    update_found = Signal(object, str)   # Release or None, "manual" / "error:..." / ""


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
        self._cum: list[int] = []
        self._last_save = 0.0
        self._last_apply = 0.0
        self._parsed: OrderedDict[int, object] = OrderedDict()
        self._loader = _Loader()
        self._loader.loaded.connect(self._parsed_ready)
        self._loader.update_found.connect(self._update_checked)
        self._release = None
        updater.cleanup_old()
        self._waiting: dict[int, list] = {}
        self.history: list[tuple[str, dict]] = []
        self.cur_page, self.cur_ctx = "", {}
        self._sleep_end: float | None = None
        self._sleep_chapter: int | None = None
        L.libraries(settings)   # migrate old folder list
        from . import shelf as shelf_mod
        shelf_mod.set_shelf_style(settings.get("shelf_style", "Walnut"))
        shelf_mod.SHOW_TITLES = bool(settings.get("show_titles", False))

        self.setWindowTitle(APP_NAME)
        icon = resource_dir() / "assets" / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1440, 900)
        self.setMinimumSize(1100, 680)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_appbar())
        mid = QHBoxLayout()
        mid.setSpacing(0)
        mid.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        mid.addWidget(self.pages, 1)
        v.addLayout(mid, 1)
        self.player_bar = PlayerBar()
        self.player_bar.hide()
        v.addWidget(self.player_bar)
        self.setCentralWidget(central)
        self._build_pages()
        from .search import SearchPopup
        self.search_popup = SearchPopup(self)
        self.search_popup.picked.connect(self._search_picked)
        self.search.installEventFilter(self)
        self.search.returnPressed.connect(self._search_enter)

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_label = muted("")
        sb.addWidget(self.status_label, 1)
        self.jobs = JobManager(self)
        self.jobs_panel = JobsPanel(self.jobs, on_show=self._reveal_path, parent=self)
        self.jobs_summary = QPushButton("")
        self.jobs_summary.setObjectName("Link")
        self.jobs_summary.setCursor(Qt.PointingHandCursor)
        self.jobs_summary.clicked.connect(self._toggle_jobs)
        sb.addPermanentWidget(self.jobs_summary)
        self.jobs.changed.connect(self._jobs_changed)
        self.jobs.finished.connect(self._notify_job)
        self._make_tray()

        n = self.narrator
        n.unitStarted.connect(self._on_unit)
        n.stateChanged.connect(self._on_state)
        n.status.connect(self._status)
        n.error.connect(lambda msg: QMessageBox.warning(self, "Narration", msg))
        n.set_volume(float(settings["volume"]))
        n.set_speed(float(settings["speed"]))
        self._wire_player()
        self._shortcuts()
        QApplication.instance().installEventFilter(self)
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setInterval(1000)
        self._sleep_timer.timeout.connect(self._sleep_tick)

        geo = settings["window_geometry"]
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        self._fill_library_combo()
        self.navigate("home", push=False)
        self._warm = threading.Event()
        QTimer.singleShot(800, self._warm_up)
        self._startup_timer = QTimer(self)        # library jobs start once the voice model is ready
        self._startup_timer.setInterval(500)
        self._startup_started = time.time()
        self._startup_timer.timeout.connect(self._maybe_start_jobs)
        self._startup_timer.start()
        if self.settings.get("check_updates", True) and (updater.can_install() or os.environ.get("ABR_UPDATE_TEST")):
            QTimer.singleShot(8000, self._check_updates)

    # =========================================================================== startup
    def _warm_up(self) -> None:
        vid = self.tts.resolve(self.settings["narrator_voice"])
        if self.tts.is_loaded(vid):
            self._warm.set()
            return

        def run():
            self._quiet(self.tts.ensure_loaded, [vid])
            self._warm.set()
        threading.Thread(target=run, daemon=True).start()

    def _maybe_start_jobs(self) -> None:
        if self._warm.is_set() or time.time() - self._startup_started > 45:
            self._startup_timer.stop()
            self._startup_jobs()

    @staticmethod
    def _quiet(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - surfaced later when the user presses Play
            pass

    def _startup_jobs(self) -> None:
        todo = self.library.needs_details()
        if todo:
            def run(progress, cancelled):
                for k, bid in enumerate(todo):
                    if cancelled():
                        break
                    try:
                        self.library.fill_details(bid)
                    except Exception:  # noqa: BLE001
                        pass
                    if k % 25 == 0:
                        progress(k / len(todo), f"{k}/{len(todo)}")
                return len(todo)
            job = Job("Updating series & descriptions", run)
            job.ended.connect(lambda: (self.refresh_page(keep_scroll=True), self._rescan_folders()))
            self.jobs.run(job)
        else:
            self._rescan_folders()

    # =========================================================================== layout
    def _build_appbar(self) -> QWidget:
        bar = Bar("AppBar", margins=(18, 10, 18, 10))
        bar.setFixedHeight(66)
        logo = QLabel()
        pm = QPixmap(str(resource_dir() / "assets" / "icon.png"))
        if not pm.isNull():
            pm = pm.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(2.0)
            logo.setPixmap(pm)
        bar.add(logo)
        bar.lay.addSpacing(6)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        bar.add(title)
        bar.lay.addSpacing(26)
        self.library_combo = QComboBox()
        self.library_combo.setMinimumWidth(210)
        self.library_combo.setToolTip("Library")
        self.library_combo.activated.connect(self._library_changed)
        bar.add(self.library_combo)
        bar.lay.addSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search..")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(400)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._search_now)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        bar.add(self.search)
        bar.stretch()
        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("UpdateButton")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self.show_update)
        self.update_btn.hide()
        bar.add(self.update_btn)
        bar.lay.addSpacing(6)
        add = IconButton("upload", "Add books or a folder", 19)
        add.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(self)
        m.addAction("Add books…", self.add_books)
        m.addAction("Add a folder to a library…", self.add_folder)
        m.addSeparator()
        m.addAction("Manage libraries…", lambda: self.show_settings("Libraries"))
        m.addAction("Rescan library folders", lambda: self._rescan_folders(quiet=False))
        add.setMenu(m)
        bar.add(add)
        self.jobs_button = bar.add(JobsButton())
        self.jobs_button.clicked.connect(self._toggle_jobs)
        voices = bar.add(IconButton("mic", "Voices: preview, default narrator, voice cloning", 19))
        voices.clicked.connect(self.show_voices)
        sett = bar.add(IconButton("settings", "Settings", 19))
        sett.clicked.connect(self.show_settings)
        return bar

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("SideBar")
        side.setFixedWidth(96)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(0)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QToolButton] = {}
        for key, glyph, label in NAV:
            b = QToolButton()
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setText(label)
            b.setIconSize(QSize(26, 26))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setFixedSize(96, 86)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            lay.addWidget(b)
        self._nav_icons()
        lay.addStretch(1)
        ver = QLabel(f"v{__version__}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(f"color: {theme.FAINT}; font-size: 8.5pt;")
        lay.addWidget(ver)
        return side

    def _build_pages(self) -> None:
        self.home_page = HomePage()
        self.library_page = ShelfPage()
        self.series_page = ShelfPage(filters=False, sorts=["Name", "Number of books", "Recently added"])
        self.series_books_page = ShelfPage(filters=False, sorts=None, back=True)
        self.authors_page = ShelfPage(filters=False, sorts=["Name", "Number of books"])
        self.author_books_page = ShelfPage(filters=False, sorts=None, back=True)
        self.book_page = BookPage()
        self.reader_page = self._build_reader_page()
        self.page_widgets = {
            "home": self.home_page, "library": self.library_page, "series": self.series_page,
            "series_books": self.series_books_page, "authors": self.authors_page,
            "author_books": self.author_books_page, "book": self.book_page, "reader": self.reader_page,
        }
        for w in self.page_widgets.values():
            self.pages.addWidget(w)
        s = self.settings
        lp = self.library_page
        lp.filter.setCurrentText(s.get("library_filter", "All"))
        lp.sort.setCurrentText(s.get("library_sort_name", "Title"))
        lp.desc.setChecked(bool(s.get("library_desc", False)))
        self.series_page.sort.setCurrentText(s.get("series_sort", "Name"))
        self.authors_page.sort.setCurrentText(s.get("authors_sort", "Name"))
        width = int(s.get("cover_width", 120))
        for p in (self.library_page, self.series_page, self.series_books_page, self.authors_page,
                  self.author_books_page):
            p.shelf.set_cover_width(width)
            p.zoom.set_value(width)
            p.itemActivated.connect(self._item_activated)
            p.itemPlay.connect(lambda it: self.play_book(it.key))
            p.itemMenu.connect(self._item_menu)
            p.zoomChanged.connect(self._zoom)
            p.viewChanged.connect(self._view_changed)
            p.back.connect(self.go_back)
        more = IconButton("more", "Series actions: audiobooks for the whole series, read all volumes", 15)
        more.clicked.connect(lambda: self._series_menu(self.cur_ctx.get("name", ""),
                                                       more.mapToGlobal(more.rect().bottomLeft())))
        self.series_books_page.bar.add(more)
        self.home_page.set_cover_width(width)
        self.home_page.itemActivated.connect(self._item_activated)
        self.home_page.itemPlay.connect(lambda it: self.play_book(it.key))
        self.home_page.itemMenu.connect(self._item_menu)
        bp = self.book_page
        bp.back.connect(self.go_back)
        bp.play.connect(self.play_book)
        bp.read.connect(self.read_book)
        bp.export.connect(self.export_book)
        bp.seriesClicked.connect(lambda name: self.navigate("series_books", name=name))
        bp.authorClicked.connect(lambda name: self.navigate("author_books", name=name))
        bp.moreClicked.connect(lambda bid, pos: self._book_menu(bid, pos))
        bp.charactersClicked.connect(self._characters_menu_at)

    def _build_reader_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = Bar("TopBar", margins=(10, 7, 12, 7))
        back = top.add(IconButton("back", "Back (Esc)", 15))
        back.clicked.connect(self.go_back)
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
        self.chapter_box.activated.connect(self._chapter_chosen)
        top.add(self.chapter_box)
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
        self.char_btn = IconButton("people", "Character voices", 14, text="Characters")
        self.char_btn.setPopupMode(QToolButton.InstantPopup)
        self.char_menu = QMenu(self)
        self.char_btn.setMenu(self.char_menu)
        self.char_menu.aboutToShow.connect(lambda: self._fill_char_menu(self.char_menu, self.book_id, self.book))
        top.add(self.char_btn)
        self.ocr_btn = IconButton("sparkle", "Comic and scanned-page reading", 14, text="Pages")
        self.ocr_btn.setPopupMode(QToolButton.InstantPopup)
        self.ocr_menu = QMenu(self)
        self.ocr_btn.setMenu(self.ocr_menu)
        self.ocr_menu.aboutToShow.connect(self._fill_ocr_menu)
        top.add(self.ocr_btn)
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
        self.comic_reader.pageChanged.connect(lambda p, c: self.page_label.setText(f"Page {p + 1} / {c}"))
        return page

    @staticmethod
    def _text_button(text: str, tip: str, pt: int) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f'font-family: "Georgia"; font-size: {pt}pt; padding: 2px 8px;')
        return b

    def _wire_player(self) -> None:
        pb = self.player_bar
        pb.playClicked.connect(self._toggle)
        pb.stepClicked.connect(self.narrator.step)
        pb.chapterClicked.connect(self.narrator.chapter_step)
        pb.chapterChosen.connect(self._chapter_chosen)
        pb.seek.connect(self.narrator.jump)
        pb.voiceChanged.connect(self._voice_changed)
        pb.speedChanged.connect(self._speed_changed)
        pb.volumeChanged.connect(self._volume_changed)
        pb.sleepChosen.connect(self._sleep_chosen)
        pb.openReader.connect(lambda: self.book_id and self.read_book(self.book_id))
        pb.openBook.connect(lambda: self.book_id and self.navigate("book", id=self.book_id))
        pb.closeClicked.connect(self.close_player)
        pb.charactersToggled.connect(lambda on: self.book_id and self._toggle_characters(self.book_id, on))
        pb.set_speed(float(self.settings["speed"]))
        pb.set_volume(float(self.settings["volume"]))

    def _shortcuts(self) -> None:
        def sc(keys, fn, cond=lambda: True):
            for k in (keys if isinstance(keys, (list, tuple)) else [keys]):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.WindowShortcut)
                s.activated.connect(lambda fn=fn, cond=cond: cond() and fn())
        loaded = lambda: self.book is not None  # noqa: E731
        reader = lambda: self.cur_page == "reader" and self.book is not None  # noqa: E731
        sc("Space", self._toggle, loaded)
        sc("Left", lambda: self.narrator.step(-1), loaded)
        sc("Right", lambda: self.narrator.step(1), loaded)
        sc("Ctrl+Left", lambda: self.narrator.chapter_step(-1), loaded)
        sc("Ctrl+Right", lambda: self.narrator.chapter_step(1), loaded)
        sc(["Ctrl+=", "Ctrl++"], lambda: self._font_step(1), reader)
        sc("Ctrl+-", lambda: self._font_step(-1), reader)
        sc("PgDown", lambda: self._page_step(1), reader)
        sc("PgUp", lambda: self._page_step(-1), reader)
        sc("Alt+Left", self.go_back)
        sc("Escape", self._escape)
        sc("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll()))

    def _escape(self) -> None:
        if self.search_popup.isVisible():
            self.search_popup.hide()
        elif self.search.hasFocus():
            self.search.clear()
            self.search.clearFocus()
        elif self.cur_page in ("reader", "book", "series_books", "author_books"):
            self.go_back()

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == QEvent.MouseButtonPress:
            if e.button() == Qt.BackButton:
                self.go_back()
                return True
            popup = getattr(self, "search_popup", None)
            if popup is not None and popup.isVisible() and isinstance(obj, QWidget) and obj.window() is self:
                inside = popup.isAncestorOf(obj) or obj is popup or obj is self.search
                if not inside:
                    popup.hide()
            panel = getattr(self, "jobs_panel", None)
            if panel is not None and panel.isVisible() and isinstance(obj, QWidget) and obj.window() is self:
                inside = panel.isAncestorOf(obj) or obj is panel or obj is self.jobs_button or obj is self.jobs_summary
                if not inside:
                    panel.hide()
        elif t == QEvent.KeyPress and obj is getattr(self, "search", None):
            popup = self.search_popup
            if e.key() == Qt.Key_Down and popup.isVisible():
                popup.move_selection(1)
                return True
            if e.key() == Qt.Key_Up and popup.isVisible():
                popup.move_selection(-1)
                return True
            if e.key() == Qt.Key_Escape:
                if popup.isVisible():
                    popup.hide()
                else:
                    self.search.clear()
                return True
        return False

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if getattr(self, "search_popup", None) is not None and self.search_popup.isVisible():
            self._place_search_popup()

    def _status(self, msg: str) -> None:
        self.status_label.setText(msg)

    # ---- tasks ------------------------------------------------------------------------
    def _toggle_jobs(self) -> None:
        b = self.jobs_button
        right = b.mapTo(self, b.rect().bottomRight())
        self.jobs_panel.toggle_at(QPoint(max(8, right.x() - 480), right.y() + 6))

    def _jobs_changed(self) -> None:
        running, queued = self.jobs.running(), self.jobs.queued()
        self.jobs_button.set_count(len(running) + len(queued), bool(running))
        if running:
            j = running[0]
            text = f"▶  {j.title}  ·  {int(j.frac * 100)}%"
            if len(running) > 1:
                text += f"  ·  +{len(running) - 1} more"
            if queued:
                text += f"  ·  {len(queued)} queued"
            self.jobs_summary.setText(text)
            self.jobs_summary.setToolTip(j.msg or j.title)
        else:
            self.jobs_summary.setText("")

    # ---- updates -------------------------------------------------------------------------
    def _check_updates(self, manual: bool = False) -> None:
        def run():
            try:
                self._loader.update_found.emit(updater.check(), "manual" if manual else "")
            except updater.UpdateError as e:
                self._loader.update_found.emit(None, f"error:{e}" if manual else "")
        threading.Thread(target=run, daemon=True).start()

    def _update_checked(self, rel, info: str) -> None:
        if rel is not None:
            self._release = rel
            self.update_btn.setText(f"Update  v{rel.version}")
            self.update_btn.show()
        if info == "manual":
            if rel:
                self.show_update()
            else:
                QMessageBox.information(self, "Updates", f"You have the latest version ({updater.current_version()}).")
        elif info.startswith("error:"):
            QMessageBox.warning(self, "Updates", info[6:])

    def show_update(self) -> None:
        rel = self._release
        if rel is None:
            return
        d = UpdateDialog(rel, updater.can_install(), updater.current_version(), self)
        if not d.exec():
            return
        if d.choice == "page":
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(rel.page))
        elif d.choice == "install":
            if self.jobs.busy_prefix("Downloading update"):
                return
            job = Job(f"Downloading update {rel.version}", lambda p, c: updater.download(rel, p, c))
            self.jobs.run(job, on_done=lambda path: self._apply_update(rel, path),
                          on_failed=lambda e: QMessageBox.warning(self, "Update", e))
            self._toggle_jobs() if not self.jobs_panel.isVisible() else None

    def _apply_update(self, rel, zip_path) -> None:
        busy = [j.title for j in self.jobs.running() if j.heavy]
        if busy and QMessageBox.question(
                self, "Update", f"These tasks are still running:\n• " + "\n• ".join(busy) +
                "\n\nRestart now anyway? (Exports continue where they stopped next time.)") != QMessageBox.Yes:
            self._status(f"Update {rel.version} is downloaded; it installs when you click Update again.")
            return
        try:
            exe = updater.install(Path(zip_path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Update", f"Could not install the update: {e}")
            return
        self._status(f"Version {rel.version} installed – restarting…")
        updater.restart(exe)
        self.close()

    # ---- Windows notifications ---------------------------------------------------------
    def _make_tray(self) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        menu.addAction(f"Open {APP_NAME}", self._bring_to_front)
        menu.addAction("Tasks", lambda: (self._bring_to_front(), self._toggle_jobs()))
        menu.addSeparator()
        menu.addAction("Quit", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and self._bring_to_front())
        self.tray.messageClicked.connect(lambda: (self._bring_to_front(),
                                                  None if self.jobs_panel.isVisible() else self._toggle_jobs()))
        self.tray.show()

    def _bring_to_front(self) -> None:
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()

    def _notify_job(self, job) -> None:
        """A Windows notification when a long task (export, page reading, characters) ends."""
        from PySide6.QtWidgets import QSystemTrayIcon
        if not job.heavy or self.tray is None or not self.settings.get("notify_tasks", True):
            return
        if job.state == "done":
            text = job.msg if job.msg and job.msg not in ("Done",) and not job.msg.startswith("Chapter") else \
                f"Finished in {int((job.ended_at - job.started_at) // 60)} min."
            self.tray.showMessage(job.title, text, QSystemTrayIcon.Information, 8000)
        elif job.state == "failed":
            self.tray.showMessage(f"Failed: {job.title}", job.error or "", QSystemTrayIcon.Warning, 10000)

    def _reveal_path(self, path: str) -> None:
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    # =========================================================================== data
    def rows(self) -> list[dict]:
        libs = L.libraries(self.settings)
        rows = self.library.books()
        for r in rows:
            r["library"] = L.library_of(r["path"], libs)
        return rows

    def current_library(self) -> str:
        return self.settings.get("current_library", L.ALL) or L.ALL

    def rows_in_library(self) -> list[dict]:
        return views.in_library(self.rows(), self.current_library())

    def _fill_library_combo(self) -> None:
        names = [L.ALL] + L.library_names(self.settings, self.rows())
        cur = self.current_library()
        self.library_combo.blockSignals(True)
        self.library_combo.clear()
        for n in names:
            self.library_combo.addItem(glyph_icon(theme.I["library"], 14), n)
        i = self.library_combo.findText(cur)
        self.library_combo.setCurrentIndex(max(0, i))
        self.library_combo.blockSignals(False)

    def _library_changed(self, i: int) -> None:
        self.settings["current_library"] = self.library_combo.itemText(i)
        if self.cur_page in ("book", "reader", "series_books", "author_books"):
            self.navigate("library")
        else:
            self.refresh_page()

    def parsed(self, book_id: int):
        """Parsed Book (cached; parses synchronously when needed)."""
        if book_id == self.book_id and self.book is not None:
            return self.book
        if book_id in self._parsed:
            self._parsed.move_to_end(book_id)
            return self._parsed[book_id]
        r = self.library.get(book_id)
        book = store.open_book(book_id, r["path"])
        self._remember(book_id, book)
        return book

    def _remember(self, book_id: int, book) -> None:
        self._parsed[book_id] = book
        while len(self._parsed) > 4:
            self._parsed.popitem(last=False)
        r = self.library.get(book_id)
        chars = sum(len(book.unit_text(u)) for u in range(book.unit_count()))
        if r and (r.get("chars") != chars or r.get("total") != book.unit_count()):
            self.library.update(book_id, chars=chars, total=book.unit_count())

    def parse_async(self, book_id: int, then) -> None:
        if book_id == self.book_id and self.book is not None:
            then(self.book)
            return
        if book_id in self._parsed:
            then(self._parsed[book_id])
            return
        waiting = self._waiting.setdefault(book_id, [])
        waiting.append(then)
        if len(waiting) > 1:
            return
        r = self.library.get(book_id)

        def run():
            try:
                self._loader.loaded.emit(book_id, store.open_book(book_id, r["path"]), "")
            except Exception as e:  # noqa: BLE001
                self._loader.loaded.emit(book_id, None, str(e) or e.__class__.__name__)
        threading.Thread(target=run, daemon=True).start()

    def _parsed_ready(self, book_id: int, book, err: str) -> None:
        callbacks = self._waiting.pop(book_id, [])
        if book is None:
            self._status("")
            QMessageBox.warning(self, "Open book", err)
            return
        self._remember(book_id, book)
        for cb in callbacks:
            cb(book)

    # =========================================================================== navigation
    def navigate(self, page: str, push: bool = True, **ctx) -> None:
        if push and self.cur_page and (self.cur_page, self.cur_ctx) != (page, ctx):
            self.history.append((self.cur_page, self.cur_ctx))
            self.history = self.history[-50:]
        if page != "reader" and self.cur_page == "reader":
            self._save_position()
        self.cur_page, self.cur_ctx = page, ctx
        self.pages.setCurrentWidget(self.page_widgets[page])
        nav = NAV_OF_PAGE.get(page)
        if nav:
            self.nav_buttons[nav].setChecked(True)
        elif page == "reader":       # reading: no sidebar item is "current"
            self.nav_group.setExclusive(False)
            for b in self.nav_buttons.values():
                b.setChecked(False)
            self.nav_group.setExclusive(True)
        self.refresh_page()

    def go_back(self) -> None:
        if self.history:
            page, ctx = self.history.pop()
            self.navigate(page, push=False, **ctx)
        elif self.cur_page != "home":
            self.navigate("home", push=False)

    def _search_now(self) -> None:
        from .search import match_names, rank_books
        text = self.search.text().strip()
        if self.cur_page == "library":
            self.refresh_page(keep_scroll=False)
        if not text:
            self.search_popup.hide()
            return
        if not self.search.hasFocus():
            return
        rows = self.rows_in_library()
        books = rank_books(rows, text)
        series = match_names([(n, len(b)) for n, b in views.series_groups(rows)], text)
        authors = match_names([(n, len(b)) for n, b in views.author_groups(rows)], text)
        self._place_search_popup()
        self.search_popup.show_results(books, series, authors, text)

    def _place_search_popup(self) -> None:
        pos = self.search.mapTo(self, self.search.rect().bottomLeft())
        self.search_popup.move(pos.x(), pos.y() + 4)
        self.search_popup.setFixedWidth(max(self.search.width(), 480))

    def _search_enter(self) -> None:
        if self.search_popup.isVisible() and self.search_popup.activate_current():
            return
        self.search_popup.hide()
        if self.search.text().strip():
            self.navigate("library")

    def _search_picked(self, kind: str, key) -> None:
        if kind == "all":
            self.navigate("library")
            return
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.clearFocus()
        if kind == "book":
            self.navigate("book", id=key)
        elif kind == "series":
            self.navigate("series_books", name=key)
        elif kind == "author":
            self.navigate("author_books", name=key)

    def refresh_page(self, keep_scroll: bool = False) -> None:
        page, ctx = self.cur_page, self.cur_ctx
        title_lib = "" if self.current_library() == L.ALL else f"  ·  {self.current_library()}"
        if page == "home":
            rows = self.rows_in_library()
            shelves = [(t, [book_item(r, with_index=(t == "Continue Series")) for r in books])
                       for t, books in views.home_shelves(rows)]
            self.home_page.set_shelves(shelves)
        elif page == "library":
            lp = self.library_page
            rows = views.search(self.rows_in_library(), self.search.text())
            rows = views.apply_filter(rows, lp.filter.currentText())
            rows = views.apply_sort(rows, lp.sort.currentText(), lp.desc.isChecked())
            label = f"{len(rows)} Book{'s' if len(rows) != 1 else ''}"
            if self.search.text().strip():
                label += f"  ·  “{self.search.text().strip()}”"
            lp.set_items([book_item(r, with_index=lp.sort.currentText() == "Series") for r in rows],
                         label + title_lib, keep_scroll)
        elif page == "series":
            sp = self.series_page
            groups = views.series_groups(self.rows_in_library())
            key = sp.sort.currentText()
            if key == "Number of books":
                groups.sort(key=lambda g: -len(g[1]))
            elif key == "Recently added":
                groups.sort(key=lambda g: -max(b.get("added") or 0 for b in g[1]))
            items = [ShelfItem("series", name, name, covers=[b.get("cover") for b in books[:3]], count=len(books))
                     for name, books in groups]
            sp.set_items(items, f"{len(items)} Series{title_lib}", keep_scroll)
        elif page == "series_books":
            name = ctx.get("name", "")
            books = dict(views.series_groups(self.rows())).get(name, [])
            self.series_books_page.set_items([book_item(r, with_index=True) for r in books],
                                             f"{name}  ·  {len(books)} book{'s' if len(books) != 1 else ''}",
                                             keep_scroll)
        elif page == "authors":
            ap = self.authors_page
            groups = views.author_groups(self.rows_in_library())
            if ap.sort.currentText() == "Number of books":
                groups.sort(key=lambda g: -len(g[1]))
            items = [ShelfItem("author", name, name, covers=[books[0].get("cover")], count=len(books))
                     for name, books in groups]
            ap.set_items(items, f"{len(items)} Authors{title_lib}", keep_scroll)
        elif page == "author_books":
            name = ctx.get("name", "")
            books = dict(views.author_groups(self.rows())).get(name, [])
            self.author_books_page.set_items([book_item(r, with_index=True) for r in books],
                                             f"{name}  ·  {len(books)} book{'s' if len(books) != 1 else ''}",
                                             keep_scroll)
        elif page == "book":
            self._show_book_page(ctx["id"])

    def _view_changed(self) -> None:
        lp = self.library_page
        self.settings["library_filter"] = lp.filter.currentText()
        self.settings["library_sort_name"] = lp.sort.currentText()
        self.settings["library_desc"] = lp.desc.isChecked()
        self.settings["series_sort"] = self.series_page.sort.currentText()
        self.settings["authors_sort"] = self.authors_page.sort.currentText()
        self.refresh_page()

    def _zoom(self, w: int) -> None:
        self.settings["cover_width"] = w
        for p in (self.library_page, self.series_page, self.series_books_page, self.authors_page,
                  self.author_books_page):
            p.shelf.set_cover_width(w)
            p.zoom.set_value(w)
        self.home_page.set_cover_width(w)

    def _item_activated(self, it: ShelfItem) -> None:
        if it.kind == "book":
            self.navigate("book", id=it.key)
        elif it.kind == "series":
            self.navigate("series_books", name=it.key)
        elif it.kind == "author":
            self.navigate("author_books", name=it.key)

    def _item_menu(self, it: ShelfItem, pos) -> None:
        if it.kind == "book":
            self._book_menu(it.key, pos)
        elif it.kind == "series":
            self._series_menu(it.key, pos)

    def _book_menu(self, book_id: int, pos) -> None:
        r = self.library.get(book_id)
        if not r:
            return
        m = QMenu(self)
        m.addAction("Play", lambda: self.play_book(book_id))
        m.addAction("Read", lambda: self.read_book(book_id))
        m.addAction("Open book page", lambda: self.navigate("book", id=book_id))
        m.addAction("Make audiobook (M4B)…", lambda: self.export_book(book_id))
        m.addSeparator()
        if r.get("finished"):
            m.addAction("Mark as not finished", lambda: self._mark(book_id, False))
        else:
            m.addAction("Mark as finished", lambda: self._mark(book_id, True))
        m.addAction("Show file in folder", lambda: self.reveal_book(book_id))
        m.addAction("Remove from library", lambda: self.remove_book(book_id))
        m.exec(pos)

    def _mark(self, book_id: int, finished: bool) -> None:
        r = self.library.get(book_id)
        total = r.get("total") or 0
        if finished and total < 2:
            try:
                total = self.parsed(book_id).unit_count()
            except Exception:  # noqa: BLE001
                return
        self.library.update(book_id, total=total, position=max(0, total - 1) if finished else 0,
                            opened=r.get("opened") or time.time())
        self.refresh_page(keep_scroll=True)

    # =========================================================================== book page
    def _show_book_page(self, book_id: int) -> None:
        r = self.library.get(book_id)
        if not r:
            self.go_back()
            return
        r["library"] = L.library_of(r["path"], L.libraries(self.settings))
        bs = r["settings"]
        narrator = V.find_voice(self.tts.resolve(bs.get("narrator") or self.settings["narrator_voice"]))
        n_chars = len(store.load_characters(book_id)["characters"])
        if n_chars:
            chars = f"{'On' if bs.get('use_characters') else 'Off'} · {n_chars} characters found"
        else:
            chars = "Not found yet (Characters button)" if r["fmt"] != "cbz" else ""
        speed = float(self.settings["speed"])
        self.book_page.show_book(r, r["library"], f"{narrator.name} · {narrator.detail}", chars, speed)

        def fill(book):
            if self.cur_page != "book" or self.cur_ctx.get("id") != book_id:
                return
            if not r.get("chars"):
                fresh = self.library.get(book_id)
                fresh["library"] = r["library"]
                self.book_page.show_book(fresh, r["library"], f"{narrator.name} · {narrator.detail}", chars, speed)
            chapters = []
            for ci, ch in enumerate(book.chapters):
                units = book.chapter_units(ci)
                chapters.append((ch.title, sum(len(book.unit_text(u)) for u in units)))
            current = book.chapter_of(int(r.get("position") or 0)) if r.get("started") and book.units else -1
            self.book_page.set_chapters(chapters, speed, current)
        self.parse_async(book_id, fill)

    # =========================================================================== play / read
    def play_book(self, book_id: int, chapter=None) -> None:
        if not book_id:
            return
        if self.book_id == book_id and self.book is not None:
            if chapter is not None:
                self.narrator.play(self.book.chapter_first_unit[chapter])
            else:
                self._toggle()
            return

        def go(book):
            self._load_into_player(book_id, book)
            if book.unit_count() == 0:
                self.read_book(book_id)
                return
            self.narrator.play(book.chapter_first_unit[chapter] if chapter is not None else None)
        self._status("Opening…")
        self.parse_async(book_id, go)

    def read_book(self, book_id: int) -> None:
        def go(book):
            if self.book_id != book_id:
                self._load_into_player(book_id, book)
            self.navigate("reader")
            self._show_in_reader(self.narrator.current)
            if self.book.ocr_pages_needed:
                QTimer.singleShot(200, self._offer_ocr)
        if self.book_id == book_id and self.book is not None:
            go(self.book)
        else:
            self._status("Opening…")
            self.parse_async(book_id, go)

    def _load_into_player(self, book_id: int, book, keep_position: int | None = None) -> None:
        self._save_position()
        self.narrator.stop()
        self.book, self.book_id = book, book_id
        r = self.library.get(book_id)
        self.library.update(book_id, opened=time.time(), total=book.unit_count())
        pos = keep_position if keep_position is not None else int(r["position"] or 0)
        if r.get("finished") and keep_position is None:
            pos = 0                          # finished: start over
        pos = max(0, min(pos, book.unit_count() - 1)) if book.unit_count() else 0
        store.apply_speakers(book, store.load_characters(book_id))
        self.narrator.set_book(book, self._make_planner(book, book_id), pos)
        self._cum = [0]
        for u in range(book.unit_count()):
            self._cum.append(self._cum[-1] + len(book.unit_text(u)))
        title, author = r["title"] or book.title, r["author"] or book.author
        self.book_title.setText(title)
        self.book_author.setText(author)
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
        if comic:
            from ..formats.cbz import read_page
            path = book.path
            self.reader_stack.setCurrentWidget(self.comic_reader)
            self.comic_reader.set_book(book, lambda i: read_page(path, book.pages[i]))
        else:
            self.reader_stack.setCurrentWidget(self.text_reader)
            self.text_reader.set_font(self.settings["reader_font"], int(self.settings["font_size"]))
            self.text_reader.book = None
        pb = self.player_bar
        pb.set_book(title, author, r.get("cover"), [c.title for c in book.chapters], list(book.chapter_first_unit),
                    book.unit_count())
        pb.set_voices(V.all_voices(), self.narrator.planner.narrator)
        has_chars = bool(store.load_characters(book_id)["characters"])
        pb.set_characters(has_chars, bool(r["settings"].get("use_characters")))
        pb.show()
        self._update_position(pos)
        self._status("")
        self.narrator.preload()

    def _show_in_reader(self, u: int) -> None:
        if not self.book:
            return
        if self.book.kind == "comic":
            if self.book.unit_count():
                self.comic_reader.highlight(u)
            else:
                self.comic_reader.show_page(0)
        elif self.book.chapters:
            ci = self.book.chapter_of(u) if self.book.units else 0
            if self.text_reader.ci != ci or self.text_reader.book is not self.book:
                self.text_reader.show_chapter(self.book, ci)
            self.text_reader.highlight(u, center=True)

    def close_player(self) -> None:
        self._save_position()
        self.narrator.stop()
        self._sleep_chosen(None)
        self.book, self.book_id = None, None
        self.player_bar.hide()
        if self.cur_page == "reader":
            self.go_back()
        else:
            self.refresh_page(keep_scroll=True)

    def _book_settings(self, book_id: int | None = None) -> dict:
        bid = book_id or self.book_id
        r = self.library.get(bid) if bid else None
        return r["settings"] if r else {}

    def _make_planner(self, book=None, book_id=None) -> VoicePlanner:
        book = book or self.book
        book_id = book_id or self.book_id
        bs = self._book_settings(book_id)
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
        if not self.book:
            return
        if self.book.unit_count() == 0:
            if self.book.ocr_pages_needed:
                self.read_book(self.book_id)
            return
        self.narrator.toggle()

    def _on_state(self, state: str) -> None:
        self.player_bar.set_state(state)
        if state == "loading" and self.narrator.planner:
            self._status("Generating speech…" if self.tts.is_loaded(self.narrator.planner.narrator)
                         else "Loading voice model…")
        elif state in ("playing", "paused", "stopped") and self.status_label.text().startswith(("Generating", "Loading", "Opening")):
            self._status("")

    def _on_unit(self, u: int) -> None:
        if not self.book:
            return
        if self._sleep_chapter is not None and self.book.chapter_of(u) != self._sleep_chapter:
            self.narrator.pause()
            self._sleep_chosen(None)
            self._status("Sleep timer: stopped at the end of the chapter.")
        if self.cur_page == "reader":
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
        speed = float(self.settings["speed"])
        total = self._cum[-1] if self._cum else 0
        done = self._cum[u] if u < len(self._cum) else 0
        title = self.book.chapters[ci].title if self.book.chapters else ""
        self.player_bar.set_position(u, title, ci, max(1, len(self.book.chapters)),
                                     L.est_seconds(done, speed), L.est_seconds(total - done, speed))

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
        if self.book:
            self._update_position(self.narrator.current)

    def _volume_changed(self, v: float) -> None:
        self.settings["volume"] = v
        self.narrator.set_volume(v)

    # ---- sleep timer ----------------------------------------------------------------
    def _sleep_chosen(self, v) -> None:
        self._sleep_end, self._sleep_chapter = None, None
        self._sleep_timer.stop()
        if v == "chapter" and self.book:
            self._sleep_chapter = self.book.chapter_of(self.narrator.current)
            self.player_bar.set_sleep_label("End of chapter")
        elif isinstance(v, int):
            self._sleep_end = time.time() + v * 60
            self._sleep_timer.start()
            self._sleep_tick()
        else:
            self.player_bar.set_sleep_label("")

    def _sleep_tick(self) -> None:
        if self._sleep_end is None:
            return
        left = self._sleep_end - time.time()
        if left <= 0:
            self.narrator.pause()
            self._sleep_chosen(None)
            self._status("Sleep timer: paused.")
            return
        self.player_bar.set_sleep_label(f"{int(left) // 60}:{int(left) % 60:02d}")

    # =========================================================================== library
    def add_books(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add books", "", FILE_FILTER + ";;All files (*)")
        if files:
            self._import(files, "Adding books")

    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add a folder of books")
        if not d:
            return
        names = [lib["name"] for lib in L.libraries(self.settings)]
        suggestion = L.clean_name(d)
        options = names + ([suggestion] if suggestion not in names else [])
        name, ok = QInputDialog.getItem(self, "Add folder", f"Add “{d}” to which library?\n"
                                        "(Pick one, or type a new library name.)", options,
                                        options.index(suggestion) if suggestion in options else 0, True)
        if not ok or not name.strip():
            return
        L.add_folder(self.settings, d, name.strip())
        self.settings.save()
        self._fill_library_combo()
        self._import(None, "Scanning folder", [d])

    def _rescan_folders(self, quiet: bool = True) -> None:
        folders = [f for f in L.all_folders(self.settings) if os.path.isdir(f)]
        if folders:
            self._import(None, "Updating library", folders, quiet=quiet)

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
                    bid = self.library.add(p)
                    if bid:
                        added += 1
                        if p.lower().endswith(".cbz") and self._library_rtl(p):
                            self.library.update_settings(bid, rtl=True)
                except Exception:  # noqa: BLE001 - skip unreadable files
                    pass
                progress((k + 1) / max(1, len(paths)), f"{added} new")
            return added

        def done(n):
            if n or not quiet:
                self._status(f"Added {n} book{'s' if n != 1 else ''}.")
            if n:
                self._fill_library_combo()
                self.refresh_page(keep_scroll=True)
        job = Job(title, run)
        job.done.connect(done)
        job.failed.connect(lambda e: QMessageBox.warning(self, title, e))
        self.jobs.run(job)

    def remove_book(self, book_id: int) -> None:
        r = self.library.get(book_id)
        if not r:
            return
        if QMessageBox.question(self, "Remove book", f"Remove “{r['title']}” from the library?\n\n"
                                "The book file itself is not deleted.") == QMessageBox.Yes:
            if book_id == self.book_id:
                self.close_player()
            self.library.remove(book_id)
            self._parsed.pop(book_id, None)
            if self.cur_page == "book":
                self.go_back()
            else:
                self.refresh_page(keep_scroll=True)

    def reveal_book(self, book_id: int) -> None:
        r = self.library.get(book_id)
        if r and os.path.exists(r["path"]):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(r["path"])])

    # =========================================================================== characters
    def _characters_menu_at(self, book_id: int, pos) -> None:
        try:
            book = self.parsed(book_id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Characters", str(e))
            return
        m = QMenu(self)
        self._fill_char_menu(m, book_id, book)
        m.exec(pos)

    def _fill_char_menu(self, m: QMenu, book_id: int | None, book) -> None:
        m.clear()
        if not book_id or book is None:
            return
        chars = store.load_characters(book_id)
        bs = self._book_settings(book_id)
        has = bool(chars["characters"])
        use = QAction("Speak dialogue with character voices", m, checkable=True)
        use.setChecked(bool(bs.get("use_characters")) and has)
        use.setEnabled(has)
        use.toggled.connect(lambda on: self._toggle_characters(book_id, on))
        m.addAction(use)
        m.addSeparator()
        done = set(chars["done_chapters"])
        todo = [ci for ci, ch in enumerate(book.chapters) if ci not in done and any(p.quotes for p in ch.paragraphs)]
        if not book.quote_count():
            a = m.addAction("No dialogue found in this book")
            a.setEnabled(False)
        elif self.jobs.busy_prefix("Finding characters"):
            a = m.addAction("Finding characters… (running)")
            a.setEnabled(False)
        elif todo and done:
            m.addAction(f"Continue finding characters ({len(todo)} chapters left)", lambda: self.find_characters(book_id))
        elif todo:
            m.addAction("Find characters with AI…", lambda: self.find_characters(book_id))
        else:
            m.addAction("Find characters again (start over)…", lambda: self.find_characters(book_id, restart=True))
        e = m.addAction(f"Edit character voices… ({len(chars['characters'])})", lambda: self.edit_cast(book_id))
        e.setEnabled(has)

    def _toggle_characters(self, book_id: int, on: bool) -> None:
        self.library.update_settings(book_id, use_characters=on)
        if book_id == self.book_id:
            self.narrator.set_planner(self._make_planner())
            self.player_bar.set_characters(bool(store.load_characters(book_id)["characters"]), on)
            if on:
                self.narrator.preload()
        if self.cur_page == "book":
            self.refresh_page()

    def find_characters(self, book_id: int, restart: bool = False) -> None:
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
        book = self.parsed(book_id)
        if restart:
            store.save_characters(book_id, {})
        data = store.load_characters(book_id)
        n_quotes = book.quote_count()
        if not data["done_chapters"] and n_quotes > 400:
            mins = max(1, int(n_quotes / 45 * 12 / 60))
            if QMessageBox.question(self, "Find characters",
                                    f"This book has {n_quotes:,} lines of dialogue. The local AI needs roughly "
                                    f"{mins} minutes to work out who says what (you can keep reading "
                                    "meanwhile).\n\nStart now?") != QMessageBox.Yes:
                return
        start = book.chapter_of(self.narrator.current) if (book_id == self.book_id and book.units) else 0
        r = self.library.get(book_id)

        def run(progress, cancelled):
            from ..ai.characters import analyze_book
            try:
                d = analyze_book(book, data, ollama, model, progress=progress, cancelled=cancelled,
                                 save=lambda x: store.save_characters(book_id, x), start_chapter=start)
                return d
            finally:
                ollama.unload(model)

        job = Job(f"Finding characters: {r['title']}"[:70], run, heavy=True)
        job.progress.connect(lambda f, m: self._apply_characters(book_id, live=True))
        self.library.update_settings(book_id, use_characters=True)
        self._status(f"Finding characters in “{r['title']}”…")
        self.jobs.run(job, on_done=lambda d: self._characters_done(book_id),
                      on_failed=lambda e: QMessageBox.warning(self, "Find characters", e))

    def _apply_characters(self, book_id: int, live: bool = False, final: bool = False) -> None:
        if live and time.time() - self._last_apply < 4:
            return
        self._last_apply = time.time()
        data = store.load_characters(book_id)
        bs = self._book_settings(book_id)
        keep = bs.get("cast") or {}
        if final:   # genders are known now: re-assign every voice the user did not pick by hand
            manual = set(bs.get("cast_manual") or [])
            keep = {k: v for k, v in keep.items() if k in manual}
        narrator = self.tts.resolve(bs.get("narrator") or self.settings["narrator_voice"])
        cast = auto_cast(data["characters"], narrator, keep)
        if cast != (bs.get("cast") or {}):
            self.library.update_settings(book_id, cast=cast)
        if book_id != self.book_id or not self.book:
            return
        store.apply_speakers(self.book, data)
        planner = self.narrator.planner
        if live and planner is not None:
            planner.cast.update({k: self.tts.resolve(v) for k, v in cast.items()})
            planner.use_characters = bool(data["characters"]) and bool(self._book_settings().get("use_characters"))
        else:
            self.narrator.set_planner(self._make_planner())
        self.player_bar.set_characters(bool(data["characters"]), bool(self._book_settings().get("use_characters")))

    def _characters_done(self, book_id: int) -> None:
        self._apply_characters(book_id, final=True)
        n = len(store.load_characters(book_id)["characters"])
        self._status(f"Found {n} speaking characters. Characters ▸ Edit character voices to change who sounds like whom.")
        if book_id == self.book_id:
            self.narrator.preload()
        if self.cur_page == "book" and self.cur_ctx.get("id") == book_id:
            self.refresh_page()

    def edit_cast(self, book_id: int) -> None:
        data = store.load_characters(book_id)
        bs = self._book_settings(book_id)
        narrator = self.tts.resolve(bs.get("narrator") or self.settings["narrator_voice"])
        d = CastDialog(data["characters"], bs.get("cast") or {}, narrator,
                       bool(bs.get("use_characters")), self.tts, self.preview_player, self)
        if d.exec():
            old = set() if d.reset_manual else set(bs.get("cast_manual") or [])
            manual = sorted(old | d.changed_names)
            self.library.update_settings(book_id, cast=d.result_cast(), use_characters=d.use.isChecked(),
                                         cast_manual=manual)
            if book_id == self.book_id:
                self.narrator.set_planner(self._make_planner())
                self.player_bar.set_characters(True, d.use.isChecked())
                self.narrator.preload()
            if self.cur_page == "book":
                self.refresh_page()

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
            rtl.setChecked(self._book_rtl())
            rtl.toggled.connect(self._toggle_rtl)
            m.addAction(rtl)
            m.addSeparator()
        if self.jobs.busy_prefix("Reading"):
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
        if not self.book or not self.book.ocr_pages_needed or self.jobs.busy_prefix("Reading"):
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
        if not self.book or self.jobs.busy_prefix("Reading"):
            return
        book, book_id = self.book, self.book_id
        comic = book.kind == "comic"
        rtl = self._book_rtl(book_id)
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

        r = self.library.get(book_id) or {}
        job = Job(f"Reading pages: {r.get('title', '')}"[:70], run, heavy=True)
        self.jobs.run(job, on_failed=lambda e: QMessageBox.warning(self, "Read pages", e),
                      on_ended=lambda: self._ocr_finished(book_id))

    def _ocr_finished(self, book_id: int) -> None:
        store.invalidate(book_id)
        self._parsed.pop(book_id, None)
        if book_id != self.book_id:
            return
        r = self.library.get(book_id)
        pos = self.narrator.current
        try:
            book = store.open_book(book_id, r["path"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Read pages", str(e))
            return
        self._load_into_player(book_id, book, keep_position=pos)
        if self.cur_page == "reader":
            self._show_in_reader(pos)

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
        d = ExportDialog([r], self.settings, summary, parent=self)
        if not d.exec():
            return
        out = str(d.out_path())
        holder: dict = {}

        def run(progress, cancelled):
            return self._export_one(r, out, progress, cancelled, holder)

        job = Job(title, run, on_cancel=lambda: holder.get("exp") and holder["exp"].cancel(), heavy=True)
        self.jobs.run(job, on_done=lambda path: self._export_done(path),
                      on_failed=lambda e: QMessageBox.warning(self, "Make audiobook", e))
        if self.jobs.queued():
            self._status("Queued: it starts when the job before it is done (see Tasks).")

    def _export_one(self, r: dict, out: str, progress, cancelled, holder: dict):
        """Synthesise one library book to an M4B (runs on a job thread)."""
        book_id = r["id"]
        book = self.book if (book_id == self.book_id and self.book is not None) else store.open_book(book_id, r["path"])
        book.title, book.author = r["title"] or book.title, r["author"] or book.author   # library (Calibre) names
        if book.unit_count() == 0:
            raise RuntimeError("This book has no readable text yet. For comics and scanned PDFs, "
                               "open the book and use Pages ▸ Read text first.")
        store.apply_speakers(book, store.load_characters(book_id))
        planner = self._make_planner(book, book_id)
        cover = book.cover
        if not cover:
            from ..formats import read_meta
            cover = read_meta(r["path"])[2]
        if not cover and r.get("cover") and os.path.exists(r["cover"]):
            cover = Path(r["cover"]).read_bytes()
        exp = ExportJob(book_id, book, planner, self.tts, out, speed=float(self.settings["speed"]),
                        bitrate=self.settings["export_bitrate"], cover=cover, progress=progress)
        holder["exp"] = exp
        if cancelled():
            exp.cancel()
        return exp.run()

    # =========================================================================== series batches
    def _series_books(self, name: str) -> list[dict]:
        return dict(views.series_groups(self.rows())).get(name, [])

    def _series_menu(self, name: str, pos) -> None:
        books = self._series_books(name)
        if not books:
            return
        m = QMenu(self)
        m.addAction(f"Open “{name}”", lambda: self.navigate("series_books", name=name))
        m.addSeparator()
        texts = [b for b in books if b["fmt"] != "cbz"]
        comics = [b for b in books if b["fmt"] == "cbz"]
        if texts:
            m.addAction(f"Make audiobooks for all {len(texts)} book{'s' if len(texts) != 1 else ''}…",
                        lambda: self.export_series(name))
        if comics:
            m.addAction(f"Read all {len(comics)} volume{'s' if len(comics) != 1 else ''} (OCR)…",
                        lambda: self.ocr_series(name))
            m.addAction(f"Make audiobooks for the read volumes…", lambda: self.export_series(name, comics=True))
        m.exec(pos)

    def export_series(self, name: str, comics: bool = False) -> None:
        books = [b for b in self._series_books(name) if (b["fmt"] == "cbz") == comics]
        if comics:
            books = [b for b in books if (b.get("total") or 0) > 0]   # only volumes whose pages were read
        if not books:
            QMessageBox.information(self, "Make audiobooks", "No volumes of this series have readable text yet. "
                                    "Use “Read all volumes (OCR)” first.")
            return
        title = f"Audiobooks: {name}"[:60]
        if self.jobs.busy(title):
            QMessageBox.information(self, "Make audiobooks", "This series is already being exported.")
            return
        n_chars = sum(1 for b in books if b["settings"].get("use_characters")
                      and store.load_characters(b["id"])["characters"])
        narrator = V.find_voice(self.tts.resolve(self.settings["narrator_voice"]))
        summary = (f"Narrator: each book's own narrator voice (default {narrator.name}) · "
                   f"character voices in {n_chars} of {len(books)} books · speed {float(self.settings['speed']):g}×")
        d = ExportDialog(books, self.settings, summary, series=name, parent=self)
        if not d.exec():
            return
        plan = [(b, str(d.path_for(b))) for b in books]
        skip = d.skip.isChecked()
        holder: dict = {}

        def run(progress, cancelled):
            made, skipped = [], []
            n = len(plan)
            for k, (r, out) in enumerate(plan):
                if cancelled():
                    break
                if skip and Path(out).exists():
                    skipped.append(r["title"])
                    continue
                try:
                    self._export_one(r, out, lambda f, m, k=k: progress((k + f) / n, f"Book {k + 1}/{n} · {m}"),
                                     cancelled, holder)
                    made.append(out)
                except Exception as e:  # noqa: BLE001 - keep going with the next book
                    if e.__class__.__name__ == "Cancelled":
                        break
                    skipped.append(f"{r['title']} ({e})")
            return made, skipped

        job = Job(title, run, on_cancel=lambda: holder.get("exp") and holder["exp"].cancel(), heavy=True)
        self.jobs.run(job, on_done=lambda res: self._series_export_done(name, *res),
                      on_failed=lambda e: QMessageBox.warning(self, "Make audiobooks", e))
        if self.jobs.queued():
            self._status("Queued: it starts when the job before it is done (see Tasks).")

    def _series_export_done(self, name: str, made: list, skipped: list) -> None:
        msg = f"“{name}”: {len(made)} audiobook{'s' if len(made) != 1 else ''} made"
        if skipped:
            msg += f", {len(skipped)} skipped (already there or no text)"
        self._status(msg + ".  (Tasks ▸ Show file)")
        for j in self.jobs.history:          # the details go into the task's line in the panel
            if j.title == f"Audiobooks: {name}"[:60] and j.state in ("running", "done"):
                j.msg = msg

    def ocr_series(self, name: str) -> None:
        vols = [b for b in self._series_books(name) if b["fmt"] == "cbz"]
        if not vols:
            return
        if self.jobs.busy_prefix("Reading"):
            QMessageBox.information(self, "Read volumes", "Pages are already being read. Try again when that is done.")
            return
        ollama, vision = self._vision_ready()
        if not vision and QMessageBox.question(
                self, "Read volumes", "The vision model is not installed, so the basic OCR would be used "
                "(it can miss words and get the bubble order wrong).\n\nContinue anyway?") != QMessageBox.Yes:
            return
        model = self.settings["vision_model"]
        rtl = {v["id"]: self._book_rtl(v["id"]) for v in vols}

        def run(progress, cancelled):
            from ..ai.ocr import PageReader
            from ..formats.cbz import read_page
            reader = PageReader(ollama if vision else None, model if vision else None)
            n = len(vols)
            done_ids = []
            try:
                for k, r in enumerate(vols):
                    if cancelled():
                        break
                    book = store.open_book(r["id"], r["path"])
                    ocr = store.load_ocr(r["id"])
                    todo = [i for i in range(len(book.pages)) if i not in ocr]
                    for j, p in enumerate(todo):
                        if cancelled():
                            break
                        try:
                            ocr[p] = reader.comic_page(read_page(r["path"], book.pages[p]), rtl[r["id"]])
                        except Exception:  # noqa: BLE001 - one bad page must not stop the series
                            continue
                        store.save_ocr(r["id"], ocr)
                        progress((k + (j + 1) / max(1, len(todo))) / n,
                                 f"Volume {k + 1}/{n} · page {j + 1}/{len(todo)}")
                    store.invalidate(r["id"])
                    book = store.open_book(r["id"], r["path"])
                    self.library.update(r["id"], total=book.unit_count())
                    done_ids.append(r["id"])
                    progress((k + 1) / n, f"Volume {k + 1}/{n} done")
            finally:
                if vision and ollama:
                    ollama.unload(model)
            return done_ids

        job = Job(f"Reading series: {name}"[:60], run, heavy=True)
        self.jobs.run(job, on_done=lambda ids: self._series_ocr_done(name, ids),
                      on_failed=lambda e: QMessageBox.warning(self, "Read volumes", e))

    def _series_ocr_done(self, name: str, ids: list) -> None:
        for bid in ids:
            self._parsed.pop(bid, None)
            if bid == self.book_id:
                self._ocr_finished(bid)
        self._status(f"“{name}”: {len(ids)} volume{'s' if len(ids) != 1 else ''} read and ready to narrate.")
        self.refresh_page(keep_scroll=True)

    def _export_done(self, path) -> None:
        self._status(f"Audiobook ready: {Path(str(path)).name}  (Tasks ▸ Show file)")

    # =========================================================================== dialogs
    def show_voices(self) -> None:
        d = VoicesDialog(self.tts, self.preview_player, self.settings, self)
        d.exec()
        if d.changed and self.book:
            self.player_bar.set_voices(V.all_voices(), self.narrator.planner.narrator)
            self.narrator.set_planner(self._make_planner())

    def show_settings(self, start_tab: str = "") -> None:
        try:
            models = Ollama(self.settings["ollama_url"]).models()
        except OllamaError:
            models = []
        counts: dict[str, int] = {}
        for r in self.rows():
            counts[r["library"]] = counts.get(r["library"], 0) + 1
        d = SettingsDialog(self.settings, models, counts, start_tab, self)
        accepted = d.exec()
        if d.repair_requested:
            from ..setup_runtime import launch_app
            launch_app("--setup")
            self.close()
            return
        if d.found_release is not None:
            self._update_checked(d.found_release, "")
        if accepted:
            self._apply_appearance()
            self._apply_library_changes(d.old_libraries, self.settings["libraries"])
            if self.book:
                self.narrator.set_planner(self._make_planner())
                if self.book.kind != "comic":
                    self.text_reader.set_font(self.settings["reader_font"], int(self.settings["font_size"]))

    # =========================================================================== appearance
    def _apply_appearance(self) -> None:
        from . import shelf as shelf_mod
        theme.apply_theme(QApplication.instance(), self.settings.get("theme", "Audiobookshelf"))
        shelf_mod.set_shelf_style(self.settings.get("shelf_style", "Walnut"))
        shelf_mod.SHOW_TITLES = bool(self.settings.get("show_titles", False))
        for p in (self.library_page, self.series_page, self.series_books_page, self.authors_page,
                  self.author_books_page):
            p.shelf.show_titles = shelf_mod.SHOW_TITLES
            p.shelf.viewport().update()
        self.home_page.show_titles = shelf_mod.SHOW_TITLES
        self._nav_icons()
        self._fill_library_combo()
        self.player_bar.restyle()
        self.text_reader.restyle()
        self.comic_reader.restyle()
        self.book_page.restyle()
        self.jobs_panel.restyle()
        self.refresh_page(keep_scroll=True)

    def _nav_icons(self) -> None:
        for key, glyph, _ in NAV:
            ic = QIcon()
            ic.addPixmap(glyph_icon(theme.I[glyph], 24, theme.TEXT).pixmap(48, 48), QIcon.Normal, QIcon.Off)
            ic.addPixmap(glyph_icon(theme.I[glyph], 24, theme.YELLOW).pixmap(48, 48), QIcon.Normal, QIcon.On)
            self.nav_buttons[key].setIcon(ic)

    # =========================================================================== library management
    def _library_rtl(self, path: str) -> bool:
        name = L.library_of(path, L.libraries(self.settings))
        return any(lib.get("rtl") for lib in L.libraries(self.settings) if lib["name"] == name)

    def _book_rtl(self, book_id: int | None = None) -> bool:
        bid = book_id or self.book_id
        r = self.library.get(bid) if bid else None
        if not r:
            return False
        bs = r["settings"]
        return bool(bs["rtl"]) if "rtl" in bs else self._library_rtl(r["path"])

    def _apply_library_changes(self, old: list[dict], new: list[dict]) -> None:
        old_folders = {f for lib in old for f in lib["folders"]}
        new_folders = {f for lib in new for f in lib["folders"]}
        removed = old_folders - new_folders
        added = new_folders - old_folders
        if removed:
            gone = [r for r in self.rows() if r["library"] == L.OTHER and
                    any(os.path.normcase(r["path"]).startswith(os.path.normcase(f.rstrip("\\/")) + os.sep)
                        for f in removed)]
            if gone and QMessageBox.question(
                    self, "Libraries", f"{len(gone)} book{'s are' if len(gone) != 1 else ' is'} no longer in any "
                    "library folder. Remove them from the app?\n\n(The files stay on disk.)") == QMessageBox.Yes:
                for r in gone:
                    if r["id"] == self.book_id:
                        self.close_player()
                    self.library.remove(r["id"])
                    self._parsed.pop(r["id"], None)
        # right-to-left default for the comics of each library
        old_rtl = {lib["name"]: bool(lib.get("rtl")) for lib in old}
        for lib in new:
            if old_rtl.get(lib["name"]) != bool(lib.get("rtl")):
                for r in self.rows():
                    if r["library"] == lib["name"] and r["fmt"] == "cbz":
                        self.library.update_settings(r["id"], rtl=bool(lib.get("rtl")))
        if self.current_library() not in [lib["name"] for lib in new] + [L.ALL, L.OTHER]:
            self.settings["current_library"] = L.ALL
        self.settings.save()
        self._fill_library_combo()
        if added:
            self._import(None, "Scanning folders", sorted(added))
        self.refresh_page(keep_scroll=True)

    # =========================================================================== shutdown
    def closeEvent(self, e) -> None:
        self._save_position()
        self.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        self.jobs.cancel_all()
        self.narrator.shutdown()
        self.preview_player.close()
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        super().closeEvent(e)
