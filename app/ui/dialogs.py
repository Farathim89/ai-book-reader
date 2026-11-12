"""Dialogs: voices (preview + cloning), character cast, settings."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QFontComboBox)

from ..tts import voices as V
from .voice_picker import VoiceCombo
from . import theme
from .widgets import IconButton, muted

def QApplication_process() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


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
class CloneOptions(QWidget):
    """Quality (full model / Turbo) and expressiveness of a cloned voice."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QSlider
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.model = QComboBox()
        for key, label in V.CLONE_MODELS.items():
            self.model.addItem(label, key)
        self.model.setToolTip("Best uses the full Chatterbox model: closest to the recording, about a third slower. "
                              "It downloads once (3.2 GB) the first time.")
        self.ex = QSlider(Qt.Horizontal)
        self.ex.setRange(25, 100)
        self.ex.setValue(50)
        self.ex.setFixedWidth(150)
        self.ex.setToolTip("How lively the voice reads: left = calm and even, right = more emotion. 50 sounds most "
                           "like the recording.")
        self.ex_label = muted("")
        lay.addWidget(muted("Quality"))
        lay.addWidget(self.model)
        lay.addSpacing(10)
        lay.addWidget(muted("Expressiveness"))
        lay.addWidget(self.ex)
        lay.addWidget(self.ex_label)
        lay.addStretch(1)
        self.model.currentIndexChanged.connect(self._changed)
        self.ex.valueChanged.connect(self._label)
        self.ex.sliderReleased.connect(self.changed.emit)
        self._label()

    def _label(self, *_):
        v = self.ex.value()
        word = "calm" if v < 40 else "natural" if v <= 60 else "lively" if v <= 80 else "very lively"
        self.ex_label.setText(f"{v / 100:.2f}  {word}")
        self.ex.setEnabled(self.model.currentData() == "full")
        if not self.ex.isSliderDown():
            self.changed.emit()

    def _changed(self, *_):
        self._label()

    def values(self) -> tuple[str, float]:
        return self.model.currentData(), round(self.ex.value() / 100, 2)

    def set_values(self, model: str, expressiveness: float) -> None:
        for w in (self.model, self.ex):
            w.blockSignals(True)
        self.model.setCurrentIndex(max(0, self.model.findData(model)))
        self.ex.setValue(int(round(expressiveness * 100)))
        for w in (self.model, self.ex):
            w.blockSignals(False)
        v = self.ex.value()
        self.ex_label.setText(f"{v / 100:.2f}  " + ("calm" if v < 40 else "natural" if v <= 60 else
                                                     "lively" if v <= 80 else "very lively"))
        self.ex.setEnabled(model == "full")


class CloneDialog(QDialog):
    _done = Signal(object, str)          # (voice or None, error) from the worker thread
    _prog = Signal(float, str)           # (0..1, what it is doing) from the worker thread
    _found = Signal(object, str)         # (find_best_clip result or None, error)

    def __init__(self, parent=None, voice_id: str | None = None):
        super().__init__(parent)
        self.voice_id = voice_id                 # editing an existing cloned voice (same id is kept)
        self.setWindowTitle("Edit cloned voice" if voice_id else "Add a cloned voice")
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
        start_row = QHBoxLayout()
        start_row.addWidget(self.start, 1)
        self.find_btn = QPushButton("Find the best part")
        self.find_btn.setToolTip("Listens through the whole recording and picks clean narration in the main voice "
                                 "(skips intros, music, other speakers and character voices)")
        self.find_btn.clicked.connect(self._find_best)
        start_row.addWidget(self.find_btn)
        self.found = muted("")
        self.found.setWordWrap(True)
        self.found.hide()
        form.addRow("Name", self.name)
        form.addRow("Gender", self.gender)
        form.addRow("Recording", row)
        form.addRow("Start at", start_row)
        form.addRow("", self.found)
        form.addRow("Use", self.length)
        lay.addLayout(form)
        self.options = CloneOptions()
        lay.addWidget(self.options)
        tip = muted("Tip: pick calm narration (not dialogue or character voices). Chatterbox listens mostly to "
                    "the first 10 seconds of the clip.")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        from PySide6.QtWidgets import QProgressBar
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        self.step = muted("")
        self.step.hide()
        lay.addWidget(self.bar)
        lay.addWidget(self.step)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_text = "Save" if voice_id else "Add voice"
        bb.button(QDialogButtonBox.Ok).setText(self.ok_text)
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.bb = bb
        self.voice: V.Voice | None = None
        self._done.connect(self._finished)
        self._prog.connect(self._on_progress)
        self._found.connect(self._found_done)
        self.orig = None
        if voice_id:
            info = V.clone_info(voice_id)
            self.orig = (info["source"], float(info["start"]), int(info["seconds"]))
            self.name.setText(info["name"])
            self.gender.setCurrentText(info["gender"])
            self.file.setText(info["source"])
            self.start.setValue(info["start"])
            self.length.setValue(max(6, min(60, info["seconds"])))
            self.options.set_values(info["model"], info["expressiveness"])
            import os
            if info["source"] and not os.path.exists(info["source"]):
                gone = muted("The original recording isn't there any more. The voice keeps its current clip; "
                             "to cut a new one, pick a recording with Browse…")
                gone.setWordWrap(True)
                gone.setStyleSheet(f"color: {theme.YELLOW};")
                lay.insertWidget(lay.indexOf(self.options) + 1, gone)

    def _recut(self) -> bool:
        """Editing: does the clip have to be cut again (other recording, start or length)?"""
        if not self.orig:
            return True
        src, start, secs = self.orig
        return (self.file.text() != src or abs(self.start.value() - start) > 0.01
                or self.length.value() != secs)

    def _browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Voice recording", "",
                                           "Audio (*.wav *.mp3 *.m4a *.m4b *.flac *.ogg *.opus *.aac *.wma *.mp4 *.mkv *.webm);;All files (*)")
        if f:
            self.file.setText(f)
            if not self.name.text():
                from pathlib import Path
                self.name.setText(Path(f).stem[:30])
            from ..tts.clip_finder import media_duration
            if (media_duration(f) or 0) > 180:          # long recordings: the opening is often not the voice
                self._find_best()

    def _find_best(self) -> None:
        path = self.file.text()
        if not path:
            QMessageBox.information(self, self.windowTitle(), "Choose a recording first.")
            return
        import threading
        self.find_btn.setEnabled(False)
        self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
        self.bar.setValue(0)
        self.bar.show()
        self.step.setText("Listening through the recording…")
        self.step.show()
        seconds = self.length.value()

        def work():
            from ..tts.clip_finder import find_best_clip
            try:
                self._found.emit(find_best_clip(path, seconds, progress=lambda f, m: self._prog.emit(f, m)), "")
            except Exception as e:  # noqa: BLE001
                self._found.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _found_done(self, result, err: str) -> None:
        self.find_btn.setEnabled(True)
        self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
        self.bar.hide()
        self.step.hide()
        if result is None:
            QMessageBox.warning(self, self.windowTitle(), f"Could not listen through the recording: {err}")
            return
        s = int(result["start"])
        self.start.setValue(result["start"])
        where = f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
        self.found.setText(f"Picked {where} of {result['windows']} places: clean narration in the main voice "
                           f"(voice match {result['typical']:.2f}, clean {int(result['clean'] * 100)}%).")
        self.found.show()

    def _accept(self) -> None:
        if not self.name.text().strip() or not self.file.text():
            QMessageBox.information(self, "Add voice", "Enter a name and choose a recording.")
            return
        import os
        import threading
        recut = self._recut()
        if recut and not os.path.exists(self.file.text()):
            QMessageBox.information(self, self.windowTitle(), "That recording isn't there any more. Pick it again "
                                    "with Browse…, or put Start at and Use back to keep the current clip.")
            return
        ok = self.bb.button(QDialogButtonBox.Ok)
        ok.setEnabled(False)
        ok.setText("Saving…" if self.voice_id else "Adding…")
        self.bar.setValue(0)
        self.bar.show()
        self.step.setText("Starting…")
        self.step.show()
        args = (self.name.text().strip(), self.gender.currentText(), self.file.text(), self.start.value(),
                self.length.value())
        model, ex = self.options.values()
        prog = lambda f, m: self._prog.emit(f, m)  # noqa: E731
        vid = self.voice_id

        def work():                     # off the window's thread: the window stays usable meanwhile
            try:
                if vid:
                    voice = V.update_clone(vid, *args, progress=prog, model=model, expressiveness=ex, recut=recut)
                else:
                    voice = V.add_clone(*args, progress=prog, model=model, expressiveness=ex)
                self._done.emit(voice, "")
            except Exception as e:  # noqa: BLE001
                self._done.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, f: float, msg: str) -> None:
        self.bar.setValue(int(max(0.0, min(1.0, f)) * 100))
        self.step.setText(msg)

    def _finished(self, voice, err: str) -> None:
        if voice is None:
            ok = self.bb.button(QDialogButtonBox.Ok)
            ok.setEnabled(True)
            ok.setText(self.ok_text)
            self.bar.hide()
            self.step.hide()
            QMessageBox.warning(self, self.windowTitle(), err or "Could not read the recording.")
            return
        self.voice = voice
        self.bar.setValue(100)
        self.accept()


class TrainDialog(QDialog):
    """Train a cloned voice on more of the person's recordings (voice_training.py)."""

    def __init__(self, voice_id: str, parent=None):
        super().__init__(parent)
        info = V.clone_info(voice_id)
        self.setWindowTitle(f"Train “{info['name']}” on more audio")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        intro = muted("A short clip gives the sound of a voice. Training on a longer recording of the same person "
                      "(the audiobook they narrated) teaches the voice how they read: pace, rhythm and acting. "
                      "Pick a recording with mostly this one voice.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        row = QHBoxLayout()
        self.file = QLineEdit(info["source"])
        self.file.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.file, 1)
        row.addWidget(browse)
        form.addRow("Recording", row)
        self.minutes = QComboBox()
        for m, label in ((15, "15 minutes  (quick)"), (30, "30 minutes  (recommended)"), (60, "60 minutes  (most)")):
            self.minutes.addItem(label, m)
        self.minutes.setCurrentIndex(1)
        form.addRow("Use", self.minutes)
        lay.addLayout(form)
        note = muted("It runs in the background (Tasks panel) and takes roughly as long as the minutes you pick, on "
                     "your graphics card; reading aloud waits meanwhile. The first time it also downloads the "
                     "speech recogniser (1.6 GB). Only for your own listening: don't share voices of real people.")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Start training")
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Recording to train on", "",
                                           "Audio (*.m4b *.m4a *.mp3 *.wav *.flac *.ogg *.opus *.aac *.mp4 *.mkv);;All files (*)")
        if f:
            self.file.setText(f)

    def _accept(self) -> None:
        import os
        if not self.file.text() or not os.path.exists(self.file.text()):
            QMessageBox.information(self, self.windowTitle(), "Pick the recording to train on.")
            return
        from ..tts.clip_finder import media_duration
        if (media_duration(self.file.text()) or 0) < 300:
            QMessageBox.information(self, self.windowTitle(), "That recording is too short: training needs at least "
                                    "5 minutes of this voice (30 minutes or more is best).")
            return
        self.accept()

    def values(self) -> tuple[str, int]:
        return self.file.text(), int(self.minutes.currentData())


class VoicesDialog(QDialog):
    _news = Signal(str)

    def __init__(self, tts, player, settings, parent=None, train=None):
        super().__init__(parent)
        self.train_cb = train                    # main window: start a "Training voice" task
        self.setWindowTitle("Voices")
        self.resize(720, 560)
        self.tts, self.settings = tts, settings
        self.preview = Previewer(tts, player)
        lay = QVBoxLayout(self)
        head = QLabel("Voices")
        head.setStyleSheet("font-size: 15pt; font-weight: 600;")
        lay.addWidget(head)
        sub = muted("Kokoro voices are fast and clear. Cloned voices copy someone from a short recording: "
                    "Best quality sounds closest to them (about 1.8× real time on a good graphics card), "
                    "Fast is a little quicker (about 2.8×).")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.clone_opts = CloneOptions()          # shown for cloned voices
        self.clone_opts.changed.connect(self._clone_opts_changed)
        self.clone_opts.hide()
        lay.addWidget(self.clone_opts)
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
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.setToolTip("Change the name, gender, recording, start or length of this cloned voice. "
                                 "Books and characters that use it keep it.")
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setObjectName("Danger")
        self.del_btn.setToolTip("Delete this cloned voice")
        self.del_btn.clicked.connect(self._delete)
        self.status = muted("")
        for w in (self.play_btn, self.default_btn, self.edit_btn, self.del_btn):
            row.addWidget(w)
        row.addWidget(self.status, 1)
        row.addWidget(self.add_btn)
        lay.addLayout(row)
        from PySide6.QtWidgets import QProgressBar
        self.busy = QProgressBar()             # moving bar while a voice model loads / a preview is made
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(6)
        self.busy.hide()
        lay.addWidget(self.busy)
        self.preview.on_status = self._preview_status
        self._news.connect(self._voice_news)
        tts.listeners.append(self._news.emit)          # model download / loading progress
        self.list.itemDoubleClicked.connect(lambda _: self._preview())
        self.list.currentItemChanged.connect(self._sel)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.changed = False
        self._fill()

    def _voice_news(self, text: str) -> None:
        if text:
            self.status.setText(text)
            self.busy.show()

    def _clone_opts_changed(self) -> None:
        vid = self._cur() or ""
        if vid.startswith("clone:"):
            model, ex = self.clone_opts.values()
            V.set_clone_settings(vid, model=model, expressiveness=ex)
            self.changed = True

    def done(self, r: int) -> None:
        try:
            self.tts.listeners.remove(self._news.emit)
        except ValueError:
            pass
        super().done(r)

    def _preview_status(self, msg: str) -> None:
        self.status.setText(msg + ("  (the first time takes up to half a minute)" if msg.startswith("Loading") else ""))
        self.busy.setVisible(bool(msg))
        self.play_btn.setEnabled(not msg)

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
                QTimer.singleShot(0, lambda it=it: self.list.scrollToItem(it, QListWidget.PositionAtCenter))

    def _cur(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _sel(self, *_):
        vid = self._cur() or ""
        self.del_btn.setEnabled(vid.startswith("clone:"))
        self.edit_btn.setEnabled(vid.startswith("clone:"))
        self.clone_opts.setVisible(vid.startswith("clone:"))
        if vid.startswith("clone:"):
            cs = V.clone_settings(vid)
            self.clone_opts.set_values(cs["model"], cs["expressiveness"])

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

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if not it:
            return
        self.list.setCurrentItem(it)
        vid = it.data(Qt.UserRole) or ""
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("▶  Preview", self._preview)
        m.addAction("Use as default narrator", self._make_default)
        if vid.startswith("clone:"):
            m.addSeparator()
            m.addAction("Edit…  (name, recording, start, length)", self._edit)
            if self.train_cb is not None:
                m.addAction("Train on more audio…", self._train)
            if V.clone_adapter(vid) is not None:
                m.addAction("Forget the training", self._forget_training)
            m.addAction("Delete", self._delete)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _train(self) -> None:
        vid = self._cur() or ""
        if not vid.startswith("clone:") or self.train_cb is None:
            return
        d = TrainDialog(vid, self)
        if d.exec():
            source, minutes = d.values()
            self.train_cb(vid, source, minutes)
            self.status.setText("Training started – follow it in the Tasks panel.")

    def _forget_training(self) -> None:
        vid = self._cur() or ""
        if QMessageBox.question(self, "Forget the training", "Go back to the voice as it was before training?"
                                ) == QMessageBox.Yes:
            V.forget_training(vid)
            self.tts.chatterbox_full.forget(vid)
            self.changed = True
            self._fill(vid)

    def _edit(self) -> None:
        vid = self._cur() or ""
        if not vid.startswith("clone:"):
            return
        d = CloneDialog(self, voice_id=vid)
        if d.exec() and d.voice:
            self.tts.chatterbox.forget(vid)            # the clip may be new: copy the voice again
            self.tts.chatterbox_full.forget(vid)
            self.changed = True
            self._fill(vid)
            self.preview.speak(vid, self.sample.text() or PREVIEW_TEXT)

    def _delete(self) -> None:
        vid = self._cur()
        if not vid or not vid.startswith("clone:"):
            return
        v = V.find_voice(vid)
        default = self.settings["narrator_voice"] == vid
        msg = (f"Delete the cloned voice “{v.name}”? This can't be undone.\n\n"
               "Books and characters that use it go back to the default narrator"
               + (" (it is the default now, so Heart takes over)." if default else "."))
        if QMessageBox.question(self, "Delete voice", msg) == QMessageBox.Yes:
            V.remove_clone(vid)
            self.tts.chatterbox.forget(vid)
            self.tts.chatterbox_full.forget(vid)
            if default:
                self.settings["narrator_voice"] = V.DEFAULT_VOICE
                self.settings.save()
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
            cb = VoiceCombo()
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

    def __init__(self, books: list[dict], settings, summary: str, series: str | None = None, parent=None):
        super().__init__(parent)
        from pathlib import Path
        self._Path = Path
        self.books = books
        self.series = series
        self.setWindowTitle("Make audiobooks" if series else "Make audiobook")
        self.setMinimumWidth(600)
        self.s = settings
        lay = QVBoxLayout(self)
        if series:
            head = QLabel(f"Make audiobooks for all {len(books)} books of “{series}”")
        else:
            head = QLabel(f"Make an M4B audiobook of “{books[0]['title']}”")
        head.setStyleSheet("font-size: 13pt; font-weight: 600;")
        head.setWordWrap(True)
        lay.addWidget(head)
        info = muted(summary + "\nChapters, cover and author/title tags are added. It runs in the background; "
                     "a stopped export continues where it left off." +
                     ("\nThe books are made one after another, in series order." if series else ""))
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
        self.abs = QCheckBox("Audiobookshelf folders (Author \\ Title, or Author \\ Series \\ 1 - Title)")
        self.abs.setChecked(bool(settings.get("export_abs_layout", True)))
        form.addRow("", self.abs)
        self.skip = QCheckBox("Skip books that already have an audiobook there")
        self.skip.setChecked(True)
        self.skip.setVisible(bool(series))
        form.addRow("", self.skip)
        self.paragraphs = QCheckBox("Read whole paragraphs at once (smoother flow; character voices still switch)")
        self.paragraphs.setToolTip("The voice reads a paragraph in one go instead of starting fresh at every "
                                   "sentence. Breaks between sentences come from the voice itself.")
        self.paragraphs.setChecked(bool(settings.get("export_paragraphs", True)))
        form.addRow("", self.paragraphs)
        lay.addLayout(form)
        self.preview = muted("")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(f"Make {len(books)} audiobooks" if series else "Make audiobook")
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.folder.textChanged.connect(self._update)
        self.abs.toggled.connect(self._update)
        self._update()

    def path_for(self, r: dict):
        from ..audio.export import audiobook_path
        root = self._Path(self.folder.text().strip() or self._Path.home())
        return audiobook_path(root, r.get("author") or "", r.get("title") or "", r.get("series"),
                              r.get("series_index"), self.abs.isChecked())

    def out_path(self):
        return self.path_for(self.books[0])

    def _update(self) -> None:
        if self.series and len(self.books) > 1:
            self.preview.setText(f"First: {self.path_for(self.books[0])}\nLast:  {self.path_for(self.books[-1])}")
        else:
            self.preview.setText(f"File: {self.out_path()}")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save audiobooks to", self.folder.text())
        if d:
            self.folder.setText(d)

    def _accept(self) -> None:
        out = self.out_path()
        if not self.series and out.exists() and QMessageBox.question(
                self, "Make audiobook", f"{out.name} already exists. Replace it?") != QMessageBox.Yes:
            return
        self.s["export_dir"] = self.folder.text().strip()
        self.s["export_abs_layout"] = self.abs.isChecked()
        self.s["export_paragraphs"] = self.paragraphs.isChecked()
        self.s.save()
        self.accept()


# ---------------------------------------------------------------------------------- updates
class UpdateDialog(QDialog):
    """'Update available' window: release notes, Install now / Release notes / Later."""

    def __init__(self, rel, frozen: bool, current: str, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTextBrowser
        self.setWindowTitle("Update available")
        self.resize(620, 520)
        self.choice = ""
        lay = QVBoxLayout(self)
        head = QLabel(f"Ai Book Reader {rel.version} is out")
        head.setStyleSheet("font-size: 15pt; font-weight: 600;")
        lay.addWidget(head)
        size = f"{rel.size / 1e6:.0f} MB" if rel.size else ""
        if not rel.runtime_ok:
            sub_text = (f"You have {current}. This version cannot be installed with one click: open the release "
                        "page and download the new portable app once (then move your data folder into it).")
        elif not frozen:
            sub_text = f"You have {current} and are running from source: update with git (git pull)."
        else:
            sub_text = (f"You have {current}. Install now downloads {size}, checks its SHA-256 fingerprint, "
                        "installs it and restarts. Your library, settings and voices stay as they are.")
            if getattr(rel, "needs_setup", False):
                sub_text += (" This version also needs newer AI parts: after the restart the Set up window "
                             "downloads them.")
        info = muted(sub_text)
        info.setWordWrap(True)
        lay.addWidget(info)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(rel.notes or "_No release notes._")
        notes.setStyleSheet(f"QTextBrowser {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
                            "border-radius: 6px; padding: 8px; }")
        lay.addWidget(notes, 1)
        row = QHBoxLayout()
        page = QPushButton("Release notes")
        page.clicked.connect(lambda: self._done("page"))
        later = QPushButton("Later")
        later.clicked.connect(self.reject)
        row.addWidget(page)
        row.addStretch(1)
        row.addWidget(later)
        if rel.runtime_ok and frozen:
            install = QPushButton("Install now")
            install.setObjectName("Accent")
            install.clicked.connect(lambda: self._done("install"))
            row.addWidget(install)
        lay.addLayout(row)

    def _done(self, choice: str) -> None:
        self.choice = choice
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
        self.titles = QComboBox()
        for key, label in shelf_mod.TITLE_MODES.items():
            self.titles.addItem(label, key)
        self.titles.setCurrentIndex(max(0, self.titles.findData(settings.get("title_bar", "under"))))
        self.articles = QCheckBox("Ignore “The”, “A” and “An” at the start of titles when "
                                  "sorting (“The Hobbit” under H)")
        self.articles.setChecked(bool(settings.get("sort_ignore_articles", False)))
        f.addRow("Colour theme", self.theme_box)
        f.addRow("Bookshelf", self.shelf_box)
        f.addRow("Titles", self.titles)
        f.addRow("Sorting", self.articles)
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

        # app: version, updates, notifications
        from .. import __version__
        from .. import updater
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("Version", QLabel(f"{__version__}" + ("" if updater.can_install() else "  (running from source)")))
        self.check_updates = QCheckBox("Check for updates automatically (asks GitHub for the latest release)")
        self.check_updates.setChecked(bool(settings.get("check_updates", True)))
        f.addRow("", self.check_updates)
        self.auto_update = QCheckBox("Install updates automatically (downloads in the background, "
                                     "installs when you close the app)")
        self.auto_update.setChecked(bool(settings.get("auto_update", False)))
        self.auto_update.setEnabled(self.check_updates.isChecked() and updater.can_install())
        self.check_updates.toggled.connect(lambda on: self.auto_update.setEnabled(on and updater.can_install()))
        f.addRow("", self.auto_update)
        row = QHBoxLayout()
        now = QPushButton("Check now")
        self.update_status = muted("")
        now.clicked.connect(self._check_now)
        row.addWidget(now)
        row.addWidget(self.update_status, 1)
        f.addRow("", row)
        self.notify = QCheckBox("Windows notification when an export, page reading or character search ends")
        self.notify.setChecked(bool(settings.get("notify_tasks", True)))
        f.addRow("", self.notify)
        f.addRow("", muted(f"Updates come from github.com/{updater.REPO}. No personal data is sent."))
        self.repair_requested = False
        if updater.is_lite():
            from ..setup_runtime import installed
            done = installed()
            repair = QPushButton("Repair AI parts…")
            repair.setToolTip("Closes the app and opens the Set up window to install PyTorch, the voices and OCR again")
            repair.clicked.connect(self._repair)
            f.addRow("AI parts", muted(f"Installed {done.get('date', '?')} · "
                                       f"{'NVIDIA GPU (CUDA)' if done.get('device') == 'cuda' else 'CPU only'}"))
            f.addRow("", repair)
            shortcuts = QPushButton("Add to desktop and Start menu")
            shortcuts.setToolTip("Shortcuts to this portable app (they also keep taskbar pins working)")
            shortcuts.clicked.connect(self._make_shortcuts)
            f.addRow("Shortcuts", shortcuts)
        tabs.addTab(w, "App")
        self.found_release = None

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("Accent")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for i in range(tabs.count()):
            if tabs.tabText(i) == start_tab:
                tabs.setCurrentIndex(i)

    def _make_shortcuts(self) -> None:
        from ..winshell import make_shortcuts
        try:
            done = make_shortcuts()
        except OSError as e:
            QMessageBox.warning(self, "Shortcuts", f"Could not make the shortcuts: {e}")
            return
        QMessageBox.information(self, "Shortcuts", "Added:\n" + "\n".join(str(p) for p in done))

    def _repair(self) -> None:
        if QMessageBox.question(self, "Repair AI parts", "Close Ai Book Reader and open the Set up window?\n\n"
                                "Running tasks are stopped; exports continue where they stopped next time."
                                ) == QMessageBox.Yes:
            self.repair_requested = True
            self.reject()

    def _check_now(self) -> None:
        from .. import updater
        self.update_status.setText("Checking…")
        QApplication_process()
        try:
            rel = updater.check()
        except updater.UpdateError as e:
            self.update_status.setText(str(e))
            return
        self.found_release = rel
        self.update_status.setText(f"Version {rel.version} is available – use the Update button at the top."
                                   if rel else f"You have the latest version ({updater.current_version()}).")

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
        s["check_updates"] = self.check_updates.isChecked()
        s["auto_update"] = self.auto_update.isChecked()
        s["notify_tasks"] = self.notify.isChecked()
        s["theme"] = self.theme_box.currentText()
        s["shelf_style"] = self.shelf_box.currentText()
        s["title_bar"] = self.titles.currentData()
        s["show_titles"] = self.titles.currentData() == "cover"
        s["sort_ignore_articles"] = self.articles.isChecked()
        self.old_libraries = [dict(lib, folders=list(lib["folders"])) for lib in (s.get("libraries") or [])]
        s["libraries"] = self.libraries_tab.result()
        s["library_folders"] = [f for lib in s["libraries"] for f in lib["folders"]]
        s.save()
        self.accept()
