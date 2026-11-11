"""Entry point."""
from __future__ import annotations

import logging
import os
import sys
import traceback


def main() -> int:
    from .paths import data_dir, setup_model_env
    setup_model_env()

    # pythonw / frozen exe have no console: send library chatter (tqdm, warnings) to a log file
    log_path = data_dir() / "app.log"
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        fh = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None or getattr(sys, "frozen", False):
            sys.stdout = fh
        if sys.stderr is None or getattr(sys, "frozen", False):
            sys.stderr = fh
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import warnings
    warnings.filterwarnings("ignore")

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Farathim.AiBookReader")
        except Exception:
            pass

    # small portable: the AI parts are installed by the Set up window on first start (and after
    # updates that need other versions); "--setup" opens it to repair them
    from .setup_runtime import run_setup_window, setup_needed
    need = setup_needed()
    if "--selftest" in sys.argv:
        if need:
            print("SELFTEST FAILED: the AI parts are not installed yet (start the app once to set them up)")
            hard_exit(1)
        hard_exit(selftest())
    if need or "--setup" in sys.argv:
        hard_exit(run_setup_window(need or "repair"))

    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME
    from .config import Settings
    from .library.db import Library
    from .tts.service import TTSService
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Farathim")
    settings = Settings()
    apply_theme(app, settings.get("theme", "Audiobookshelf"))

    def excepthook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        print(text, file=sys.stderr)
        try:
            QMessageBox.critical(None, APP_NAME, f"Something went wrong:\n\n{value}\n\nDetails are in {log_path}")
        except Exception:
            pass
    sys.excepthook = excepthook

    library = Library()
    tts = TTSService()
    win = MainWindow(settings, library, tts)
    win.show()

    # a book passed on the command line (e.g. "Open with")
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            book_id = library.add(arg) or (library.by_path(arg) or {}).get("id")
            if book_id:
                win.navigate("book", id=book_id)
            break

    rc = app.exec()
    library.close()
    hard_exit(rc)


def hard_exit(rc: int = 0) -> None:
    """End the process without unloading CUDA / audio DLLs one by one: their unload code can
    crash while worker threads are still alive. Everything is already saved at this point."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.TerminateProcess.restype = wintypes.BOOL
            k32.TerminateProcess(k32.GetCurrentProcess(), int(rc) & 0xFFFFFFFF)
        except Exception:
            pass
    os._exit(rc)


def selftest() -> int:
    """`Ai Book Reader.exe --selftest`: check voices, OCR and the format readers (results in data/app.log)."""
    import io
    import tempfile
    import time
    from pathlib import Path

    ok = True
    tmp = Path(tempfile.mkdtemp(prefix="abr_selftest_"))

    def step(name, fn):
        nonlocal ok
        t0 = time.time()
        try:
            r = fn()
            print(f"[ok]   {name} ({time.time() - t0:.1f}s) {'' if r is None else r}", flush=True)
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[FAIL] {name}: {e!r}", flush=True)
            traceback.print_exc()

    from .formats import load_book
    from .tts.service import TTSService
    from .tts.voices import ffmpeg_exe

    def txt():
        p = tmp / "Jane Doe - Test.txt"
        p.write_text("CHAPTER I.\n\n“Hello,” said Anna. It was late.\n\nCHAPTER II.\n\nThe end.", encoding="utf-8")
        b = load_book(str(p))
        return f"{len(b.chapters)} chapters, {b.unit_count()} sentences, {b.quote_count()} quotes"

    def pdf():
        import pymupdf
        d = pymupdf.open()
        d.new_page().insert_text((72, 72), "Chapter One. The rain fell all night.")
        d.save(str(tmp / "t.pdf"))
        return f"{load_book(str(tmp / 't.pdf')).unit_count()} sentences"

    def docx_():
        import docx
        d = docx.Document()
        d.add_heading("Chapter One", 1)
        d.add_paragraph("A short test paragraph. Another sentence.")
        d.save(str(tmp / "t.docx"))
        return f"{load_book(str(tmp / 't.docx')).unit_count()} sentences"

    def ocr():
        from PIL import Image, ImageDraw, ImageFont
        from .ai.ocr import PageReader
        img = Image.new("RGB", (800, 600), "white")
        try:
            font = ImageFont.truetype("arialbd.ttf", 48)
        except OSError:
            font = ImageFont.load_default()
        ImageDraw.Draw(img).text((100, 200), "HELLO THERE", fill="black", font=font)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return PageReader(None, None).comic_page(buf.getvalue())

    step("ffmpeg", ffmpeg_exe)
    step("txt", txt)
    step("pdf", pdf)
    step("docx", docx_)
    step("ocr", ocr)
    tts = TTSService()
    step("kokoro voice", lambda: f"{len(tts.synth('kokoro:af_heart', 'This is a self test.')) / 24000:.1f}s audio")
    step("chatterbox voice", lambda: f"{len(tts.synth('chatterbox:default', 'This is a self test.')) / 24000:.1f}s audio")
    print("SELFTEST", "PASSED" if ok else "FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    main()
