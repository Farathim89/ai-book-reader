"""Dialogs: voices (preview + cloning), character cast, settings."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QFontComboBox)

from ..tts import voices as V
from . import theme
from .widgets import IconButton, muted

PREVIEW_TEXT = ("The old lighthouse stood at the edge of the cliff. “We should go back,” she whispered, "
                "but he was already climbing the stairs.")


class _Signals(QObject):
    done = Signal(object, str)   # audio, error


class Previewer:
    """Speak a short sample with a voice (in the background) and play it."""

    def __init__(self, tts, player):
        self.tts = tts
        self.player = player
        self.sig = _Signals()
        self.sig.done.connect(self._play)
        self.busy = False
        self.on_status = lambda msg: None

    def speak(self, voice_id: str, text: str = PREVIEW_TEXT, speed: float = 1.0) -> None:
        if self.busy:
            return
        self.busy = True
        loaded = self.tts.is_loaded(voice_id)
        self.on_status("Loading voice model…" if not loaded else "Generating preview…")

        def run():
            try:
                audio = self.tts.synth(voice_id, text, speed)
                self.sig.done.emit(audio, "")
            except Exception as e:  # noqa: BLE001
                self.sig.done.emit(None, str(e))
        threading.Thread(target=run, daemon=True).start()

    def _play(self, audio, err: str) -> None:
        self.busy = False
        if err:
            self.on_status("")
            QMessageBox.warning(None, "Preview failed", err)
            return
        self.on_status("")
        self.player.clear()
        self.player.enqueue(("preview", 0), audio)
        self.player.play()


# ---------------------------------------------------------------------------------- voices
class CloneDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a cloned voice")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        info = QLabel("Pick a recording of one person speaking clearly, with no music or other voices. "
                      "10–30 seconds works best. Supported: WAV, MP3, M4A/M4B, FLAC, OGG and video files.")
        info.setWordWrap(True)
        info.setObjectName("Muted")
        lay.addWidget(info)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Grandpa, Narrator Sam")
        self.gender = QComboBox()
        self.gender.addItems(["female", "male", "neutral"])
        row = QHBoxLayout()
        self.file = QLineEdit()
        self.file.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.file, 1)
        row.addWidget(browse)
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 36000)
        self.start.setSuffix(" s")
        self.length = QSpinBox()
        self.length.setRange(6, 60)
        self.length.setValue(20)
        self.length.setSuffix(" s")
        form.addRow("Name", self.name)
        form.addRow("Gender", self.gender)
        form.addRow("Recording", row)
        form.addRow("Start at", self.start)
        form.addRow("Use", self.length)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Add voice")
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.voice: V.Voice | None = None

    def _browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Voice recording", "",
                                           "Audio (*.wav *.mp3 *.m4a *.m4b *.flac *.ogg *.opus *.aac *.wma *.mp4 *.mkv *.webm);;All files (*)")
        if f:
            self.file.setText(f)
            if not self.name.text():
                from pathlib import Path
                self.name.setText(Path(f).stem[:30])

    def _accept(self) -> None:
        if not self.name.text().strip() or not self.file.text():
            QMessageBox.information(self, "Add voice", "Enter a name and choose a recording.")
            return
        try:
            self.voice = V.add_clone(self.name.text().strip(), self.gender.currentText(), self.file.text(),
                                     self.start.value(), self.length.value())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Add voice", str(e))
            return
        self.accept()


class VoicesDialog(QDialog):
    def __init__(self, tts, player, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Voices")
        self.resize(720, 560)
        self.tts, self.settings = tts, settings
        self.preview = Previewer(tts, player)
        lay = QVBoxLayout(self)
        head = QLabel("Voices")
        head.setStyleSheet("font-size: 15pt; font-weight: 600;")
        lay.addWidget(head)
        sub = muted("Kokoro voices are fast and clear. Chatterbox voices are cloned from a short recording "
                    "and are slower to generate (about 3× real time on your GPU).")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.sample = QLineEdit(PREVIEW_TEXT)
        lay.addWidget(self.sample)
        row = QHBoxLayout()
        self.play_btn = QPushButton("▶  Preview")
        self.play_btn.clicked.connect(self._preview)
        self.default_btn = QPushButton("Use as default narrator")
        self.default_btn.clicked.connect(self._make_default)
        self.add_btn = QPushButton("Add cloned voice…")
        self.add_btn.setObjectName("Accent")
        self.add_btn.clicked.connect(self._add)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setObjectName("Danger")
        self.del_btn.clicked.connect(self._delete)
        self.status = muted("")
        for w in (self.play_btn, self.default_btn, self.del_btn):
            row.addWidget(w)
        row.addWidget(self.status, 1)
        row.addWidget(self.add_btn)
        lay.addLayout(row)
        self.preview.on_status = self.status.setText
        self.list.itemDoubleClicked.connect(lambda _: self._preview())
        self.list.currentItemChanged.connect(self._sel)
        self.changed = False
        self._fill()

    def _fill(self, select: str | None = None) -> None:
        self.list.clear()
        default = self.settings["narrator_voice"]
        for v in V.all_voices():
            star = "  ★ default" if v.id == default else ""
            it = QListWidgetItem(f"{v.name}{star}\n{v.detail}  ·  {v.engine}")
            it.setData(Qt.UserRole, v.id)
            self.list.addItem(it)
            if v.id == (select or default):
                self.list.setCurrentItem(it)

    def _cur(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _sel(self, *_):
        vid = self._cur() or ""
        self.del_btn.setEnabled(vid.startswith("clone:"))

    def _preview(self) -> None:
        if self._cur():
            self.preview.speak(self._cur(), self.sample.text() or PREVIEW_TEXT)

    def _make_default(self) -> None:
        if self._cur():
            self.settings["narrator_voice"] = self._cur()
            self.settings.save()
            self.changed = True
            self._fill(self._cur())

    def _add(self) -> None:
        d = CloneDialog(self)
        if d.exec() and d.voice:
            self.changed = True
            self._fill(d.voice.id)
            self.preview.speak(d.voice.id, self.sample.text() or PREVIEW_TEXT)

    def _delete(self) -> None:
        vid = self._cur()
        if not vid or not vid.startswith("clone:"):
            return
        v = V.find_voice(vid)
        if QMessageBox.question(self, "Delete voice", f"Delete the cloned voice “{v.name}”?") == QMessageBox.Yes:
            V.remove_clone(vid)
            self.tts.chatterbox.forget(vid)
            self.changed = True
            self._fill()


# ---------------------------------------------------------------------------------- cast
class CastDialog(QDialog):
    """Characters found in the book and the voice each one speaks with."""

    def __init__(self, characters: dict, cast: dict, narrator: str, use: bool, tts, player, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Character voices")
        self.resize(760, 560)
        self.characters = characters
        self.cast = dict(cast)
        self.preview = Previewer(tts, player)
        lay = QVBoxLayout(self)
        head = QLabel("Character voices")
        head.setStyleSheet("font-size: 15pt; font-weight: 600;")
        lay.addWidget(head)
        self.use = QCheckBox("Speak dialogue with character voices")
        self.use.setChecked(use)
        lay.addWidget(self.use)
        sub = muted("Who-says-what was worked out by the local AI and can be wrong now and then. "
                    "Dialogue it could not place is read by the narrator.")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Character", "Gender", "Lines", "Voice", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for c in (1, 2, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)
        self.voices = V.all_voices()
        self.narrator = narrator
        self.combos: dict[str, QComboBox] = {}
        self.changed_names: set[str] = set()
        self.reset_manual = False
        self.status = muted("")
        self.preview.on_status = self.status.setText
        self._fill()
        row = QHBoxLayout()
        auto = QPushButton("Re-assign all voices")
        auto.clicked.connect(self._auto)
        row.addWidget(auto)
        row.addWidget(self.status, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

    def _fill(self) -> None:
        chars = sorted(self.characters.items(), key=lambda kv: -int(kv[1].get("lines", 0)))
        self.table.setRowCount(len(chars))
        self.combos.clear()
        for r, (name, info) in enumerate(chars):
            alias = ", ".join(info.get("aliases", [])[:3])
            it = QTableWidgetItem(name + (f"   ({alias})" if alias else ""))
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(info.get("gender", "unknown")))
            self.table.setItem(r, 2, QTableWidgetItem(str(info.get("lines", 0))))
            cb = QComboBox()
            cb.addItem("Narrator voice", "")
            for v in self.voices:
                cb.addItem(v.label, v.id)
            i = cb.findData(self.cast.get(name, ""))
            cb.setCurrentIndex(max(0, i))
            cb.activated.connect(lambda _=0, n=name: self.changed_names.add(n))   # picked by hand
            self.combos[name] = cb
            self.table.setCellWidget(r, 3, cb)
            b = IconButton("play", "Preview this voice", 12)
            b.clicked.connect(lambda _=False, n=name: self._play(n))
            self.table.setCellWidget(r, 4, b)
            self.table.setRowHeight(r, 40)

    def _play(self, name: str) -> None:
        vid = self.combos[name].currentData() or self.narrator
        self.preview.speak(vid, f"“My name is {name}. I hope this voice suits me.”")

    def _auto(self) -> None:
        from ..tts.planner import auto_cast
        self.cast = auto_cast(self.characters, self.narrator, {})
        self.changed_names.clear()
        self.reset_manual = True
        self._fill()

    def result_cast(self) -> dict[str, str]:
        return {name: cb.currentData() for name, cb in self.combos.items() if cb.currentData()}


# ---------------------------------------------------------------------------------- export
class ExportDialog(QDialog):
    """Where to save the M4B (optionally Audiobookshelf's Author/Title/ layout) and what voices it uses."""

    def __init__(self, title: str, author: str, settings, summary: str, parent=None):
        super().__init__(parent)
        from pathlib import Path
        from ..audio.export import safe_filename
        self._Path, self._safe = Path, safe_filename
        self.setWindowTitle("Make audiobook")
        self.setMinimumWidth(560)
        self.s = settings
        self.title, self.author = title or "Audiobook", author or "Unknown"
        lay = QVBoxLayout(self)
        head = QLabel(f"Make an M4B audiobook of “{self.title}”")
        head.setStyleSheet("font-size: 13pt; font-weight: 600;")
        head.setWordWrap(True)
        lay.addWidget(head)
        info = muted(summary + "\nChapters, cover and author/title tags are added. It runs in the background; "
                     "a stopped export continues where it left off.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.folder = QLineEdit(settings["export_dir"])
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(b)
        form = QFormLayout()
        form.addRow("Save to", row)
        self.abs = QCheckBox("Put it in Author \\ Title \\ folders (Audiobookshelf layout)")
        self.abs.setChecked(bool(settings.get("export_abs_layout", True)))
        form.addRow("", self.abs)
        lay.addLayout(form)
        self.preview = muted("")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Make audiobook")
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.folder.textChanged.connect(self._update)
        self.abs.toggled.connect(self._update)
        self._update()

    def out_path(self):
        root = self._Path(self.folder.text().strip() or self._Path.home())
        name = self._safe(self.title) + ".m4b"
        if self.abs.isChecked():
            return root / self._safe(self.author) / self._safe(self.title) / name
        return root / (self._safe(f"{self.author} - {self.title}") + ".m4b")

    def _update(self) -> None:
        self.preview.setText(f"File: {self.out_path()}")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save audiobooks to", self.folder.text())
        if d:
            self.folder.setText(d)

    def _accept(self) -> None:
        out = self.out_path()
        if out.exists() and QMessageBox.question(self, "Make audiobook",
                                                 f"{out.name} already exists. Replace it?") != QMessageBox.Yes:
            return
        self.s["export_dir"] = self.folder.text().strip()
        self.s["export_abs_layout"] = self.abs.isChecked()
        self.s.save()
        self.accept()


# ---------------------------------------------------------------------------------- libraries
class LibrariesTab(QWidget):
    """ABS-style library management: name, folders and right-to-left comics per library."""

    def __init__(self, libs: list[dict], counts: dict[str, int], parent=None):
        super().__init__(parent)
        import copy
        self.libs = copy.deepcopy(libs)
        self.counts = counts
        self.deleted: list[str] = []
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(200)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        new = QPushButton("New library")
        new.clicked.connect(self._new)
        self.delete = QPushButton("Delete")
        self.delete.setObjectName("Danger")
        self.delete.clicked.connect(self._delete)
        row.addWidget(new)
        row.addWidget(self.delete)
        left.addLayout(row)
        lay.addLayout(left)

        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.textEdited.connect(self._renamed)
        form.addRow("Name", self.name)
        right.addLayout(form)
        right.addWidget(QLabel("Folders"))
        self.folders = QListWidget()
        right.addWidget(self.folders, 1)
        frow = QHBoxLayout()
        addf = QPushButton("Add folder…")
        addf.clicked.connect(self._add_folder)
        self.remf = QPushButton("Remove folder")
        self.remf.clicked.connect(self._remove_folder)
        frow.addWidget(addf)
        frow.addWidget(self.remf)
        frow.addStretch(1)
        right.addLayout(frow)
        self.rtl = QCheckBox("Comics in this library read right-to-left (manga)")
        self.rtl.toggled.connect(self._rtl)
        right.addWidget(self.rtl)
        self.info = muted("")
        self.info.setWordWrap(True)
        right.addWidget(self.info)
        lay.addLayout(right, 1)
        self.list.currentRowChanged.connect(self._load)
        self._fill(0)

    def _fill(self, select: int) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for lib in self.libs:
            self.list.addItem(lib["name"])
        self.list.blockSignals(False)
        if self.libs:
            self.list.setCurrentRow(max(0, min(select, len(self.libs) - 1)))
        self._load(self.list.currentRow())

    def _cur(self) -> dict | None:
        i = self.list.currentRow()
        return self.libs[i] if 0 <= i < len(self.libs) else None

    def _load(self, i: int) -> None:
        lib = self._cur()
        for w in (self.name, self.folders, self.rtl, self.remf, self.delete):
            w.setEnabled(lib is not None)
        self.folders.clear()
        if not lib:
            self.name.clear()
            self.info.setText("No libraries yet. Create one and add a folder with books.")
            return
        self.name.setText(lib["name"])
        for f in lib["folders"]:
            self.folders.addItem(f)
        self.rtl.blockSignals(True)
        self.rtl.setChecked(bool(lib.get("rtl")))
        self.rtl.blockSignals(False)
        n = self.counts.get(lib["name"], 0)
        self.info.setText(f"{n} book{'s' if n != 1 else ''}. Folders are scanned for new books when the app starts. "
                          "Removing a folder or library takes its books out of the app; the files stay on disk.")

    def _renamed(self, text: str) -> None:
        lib = self._cur()
        if lib is not None:
            old = lib["name"]
            lib["name"] = text
            if old in self.counts:
                self.counts[text] = self.counts.pop(old)
            self.list.currentItem().setText(text)

    def _new(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New library", "Library name:")
        if ok and name.strip():
            self.libs.append({"name": name.strip(), "folders": [], "rtl": False})
            self._fill(len(self.libs) - 1)
            self._add_folder()

    def _delete(self) -> None:
        lib = self._cur()
        if not lib:
            return
        n = self.counts.get(lib["name"], 0)
        if QMessageBox.question(self, "Delete library",
                                f"Delete the library “{lib['name']}”?\n\nIts {n} books are removed from the "
                                "app. The files themselves stay on disk.") != QMessageBox.Yes:
            return
        self.deleted.append(lib["name"])
        i = self.list.currentRow()
        self.libs.pop(i)
        self._fill(i)

    def _add_folder(self) -> None:
        lib = self._cur()
        if not lib:
            return
        d = QFileDialog.getExistingDirectory(self, f"Add a folder to {lib['name']}")
        if d and d not in lib["folders"]:
            lib["folders"].append(d)
            self._load(self.list.currentRow())

    def _remove_folder(self) -> None:
        lib = self._cur()
        it = self.folders.currentItem()
        if lib and it:
            lib["folders"].remove(it.text())
            self._load(self.list.currentRow())

    def _rtl(self, on: bool) -> None:
        lib = self._cur()
        if lib is not None:
            lib["rtl"] = on

    def result(self) -> list[dict]:
        out, seen = [], set()
        for lib in self.libs:
            name = lib["name"].strip() or "Library"
            base, k = name, 2
            while name.lower() in seen:
                name = f"{base} {k}"
                k += 1
            seen.add(name.lower())
            out.append({"name": name, "folders": list(lib["folders"]), "rtl": bool(lib.get("rtl"))})
        return out


# ---------------------------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, settings, ollama_models: list[str], library_counts: dict | None = None,
                 start_tab: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(820, 560)
        self.s = settings
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # libraries
        from ..library.libraries import libraries
        self.libraries_tab = LibrariesTab(libraries(settings), dict(library_counts or {}))
        tabs.addTab(self.libraries_tab, "Libraries")

        # appearance
        from . import shelf as shelf_mod
        w = QWidget()
        f = QFormLayout(w)
        self.theme_box = QComboBox()
        self.theme_box.addItems(list(theme.THEMES))
        self.theme_box.setCurrentText(settings.get("theme", "Audiobookshelf"))
        self.shelf_box = QComboBox()
        self.shelf_box.addItems(list(shelf_mod.SHELF_STYLES))
        self.shelf_box.setCurrentText(settings.get("shelf_style", "Walnut"))
        self.titles = QCheckBox("Show titles on the covers")
        self.titles.setChecked(bool(settings.get("show_titles", False)))
        f.addRow("Colour theme", self.theme_box)
        f.addRow("Bookshelf", self.shelf_box)
        f.addRow("", self.titles)
        f.addRow("", muted("Plain uses the colours of the theme. Changes apply right away."))
        tabs.addTab(w, "Appearance")

        # reading
        w = QWidget()
        f = QFormLayout(w)
        self.font = QFontComboBox()
        from PySide6.QtGui import QFont
        self.font.setCurrentFont(QFont(settings["reader_font"]))
        self.size = QSpinBox()
        self.size.setRange(10, 36)
        self.size.setValue(int(settings["font_size"]))
        f.addRow("Reading font", self.font)
        f.addRow("Text size", self.size)
        self.p_sent = self._spin(settings["pause_sentence"])
        self.p_para = self._spin(settings["pause_paragraph"])
        self.p_head = self._spin(settings["pause_heading"])
        self.p_chap = self._spin(settings["pause_chapter"])
        f.addRow("Pause after a sentence", self.p_sent)
        f.addRow("Pause after a paragraph", self.p_para)
        f.addRow("Pause after a heading", self.p_head)
        f.addRow("Pause after a chapter", self.p_chap)
        tabs.addTab(w, "Reading")

        # audiobook
        w = QWidget()
        f = QFormLayout(w)
        row = QHBoxLayout()
        self.export_dir = QLineEdit(settings["export_dir"])
        b = QPushButton("Browse…")
        b.clicked.connect(self._pick_dir)
        row.addWidget(self.export_dir, 1)
        row.addWidget(b)
        f.addRow("Save audiobooks to", row)
        self.bitrate = QComboBox()
        for br in ("48k", "64k", "96k", "128k"):
            self.bitrate.addItem(f"{br}bps AAC (mono)", br)
        self.bitrate.setCurrentIndex(max(0, self.bitrate.findData(settings["export_bitrate"])))
        f.addRow("Quality", self.bitrate)
        note = muted("64k is plenty for speech: about 28 MB per hour.")
        f.addRow("", note)
        tabs.addTab(w, "Audiobook")

        # AI
        w = QWidget()
        f = QFormLayout(w)
        self.ollama_url = QLineEdit(settings["ollama_url"])
        f.addRow("Ollama address", self.ollama_url)
        self.text_model = QComboBox()
        self.text_model.setEditable(True)
        self.vision_model = QComboBox()
        self.vision_model.setEditable(True)
        for m in ollama_models:
            self.text_model.addItem(m)
            self.vision_model.addItem(m)
        self.text_model.setCurrentText(settings["text_model"])
        self.vision_model.setCurrentText(settings["vision_model"])
        f.addRow("Character detection model", self.text_model)
        f.addRow("Comic / scan reading model", self.vision_model)
        hint = muted("Character detection works best with a 7B–14B instruct model (qwen2.5:14b is installed).\n"
                     "Comics and scanned pages need a vision model, e.g. qwen2.5vl:7b (about 6 GB).\n"
                     "Without one, a basic built-in OCR is used.")
        hint.setWordWrap(True)
        f.addRow("", hint)
        if not ollama_models:
            f.addRow("", QLabel(f"<span style='color:{theme.DANGER}'>Ollama is not running.</span>"))
        tabs.addTab(w, "AI models")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for i in range(tabs.count()):
            if tabs.tabText(i) == start_tab:
                tabs.setCurrentIndex(i)

    def _spin(self, v: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(0, 5)
        s.setSingleStep(0.05)
        s.setSuffix(" s")
        s.setValue(float(v))
        return s

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save audiobooks to", self.export_dir.text())
        if d:
            self.export_dir.setText(d)

    def _save(self) -> None:
        s = self.s
        s["reader_font"] = self.font.currentFont().family()
        s["font_size"] = self.size.value()
        s["pause_sentence"] = self.p_sent.value()
        s["pause_paragraph"] = self.p_para.value()
        s["pause_heading"] = self.p_head.value()
        s["pause_chapter"] = self.p_chap.value()
        s["export_dir"] = self.export_dir.text().strip()
        s["export_bitrate"] = self.bitrate.currentData()
        s["ollama_url"] = self.ollama_url.text().strip() or "http://127.0.0.1:11434"
        s["text_model"] = self.text_model.currentText().strip()
        s["vision_model"] = self.vision_model.currentText().strip()
        s["theme"] = self.theme_box.currentText()
        s["shelf_style"] = self.shelf_box.currentText()
        s["show_titles"] = self.titles.isChecked()
        self.old_libraries = [dict(lib, folders=list(lib["folders"])) for lib in (s.get("libraries") or [])]
        s["libraries"] = self.libraries_tab.result()
        s["library_folders"] = [f for lib in s["libraries"] for f in lib["folders"]]
        s.save()
        self.accept()
