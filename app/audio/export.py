"""Make an M4B audiobook: synthesise every chapter to WAV, then let ffmpeg encode AAC
with chapter markers, cover art and tags. Finished chapters are kept between runs, so
a cancelled export continues where it stopped."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..formats.model import Book
from ..paths import sub
from ..tts.planner import VoicePlanner
from ..tts.service import AUDIO_REV, TTSService, silence
from ..tts.voices import ffmpeg_exe

SR = 24000
CHAPTER_LEAD_IN = 1.0        # seconds of quiet at the start of each chapter (human audiobooks: 0.1-3 s)


class Cancelled(Exception):
    pass


def safe_filename(s: str) -> str:
    bad = '<>:"/\\|?*'
    s = "".join("_" if c in bad or ord(c) < 32 else c for c in s).strip().rstrip(".")
    return s[:150] or "Audiobook"


def audiobook_path(root, author: str, title: str, series: str | None = None, index=None,
                   abs_layout: bool = True) -> Path:
    """Where an M4B goes. Audiobookshelf layout: Author/Title/Title.m4b, or for a series
    Author/Series/<n> - Title/Title.m4b (ABS reads the volume number from the folder name)."""
    root = Path(root)
    a = safe_filename(author or "Unknown")
    t = safe_filename(title or "Audiobook")
    idx = f"{index:g}" if isinstance(index, (int, float)) else ""
    if abs_layout:
        if series:
            folder = safe_filename(f"{idx} - {title}" if idx else title)
            return root / a / safe_filename(series) / folder / f"{t}.m4b"
        return root / a / t / f"{t}.m4b"
    name = f"{author} - {series} {idx} - {title}" if series and idx else (
        f"{author} - {series} - {title}" if series else f"{author} - {title}")
    return root / (safe_filename(name) + ".m4b")


def ffmeta(title: str, author: str, chapters: list[tuple[str, float]]) -> str:
    def esc(v: str) -> str:
        for c in ("\\", "=", ";", "#", "\n"):
            v = v.replace(c, "\\" + c)
        return v
    lines = [";FFMETADATA1", f"title={esc(title)}", f"album={esc(title)}", f"artist={esc(author)}",
             f"album_artist={esc(author)}", "genre=Audiobook", "comment=Narrated with Ai Book Reader"]
    t = 0.0
    for name, dur in chapters:
        start, end = int(t * 1000), int((t + dur) * 1000)
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={esc(name)}"]
        t += dur
    return "\n".join(lines) + "\n"


class ExportJob:
    def __init__(self, book_id: int, book: Book, planner: VoicePlanner, tts: TTSService, out_path: str,
                 speed: float = 1.0, bitrate: str = "64k", cover: bytes | None = None,
                 progress: Callable[[float, str], None] | None = None):
        self.book_id = book_id
        self.book = book
        self.planner = planner
        self.tts = tts
        self.out_path = Path(out_path)
        self.speed = speed
        self.bitrate = bitrate
        self.cover = cover
        self.progress = progress or (lambda f, msg: None)
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _work_dir(self) -> Path:
        sig = json.dumps({"n": self.planner.narrator, "c": self.planner.cast if self.planner.use_characters else {},
                          "uc": self.planner.use_characters, "s": self.speed, "p": self.planner.pauses,
                          "u": self.book.unit_count(), "v": AUDIO_REV}, sort_keys=True)
        h = hashlib.sha1(sig.encode()).hexdigest()[:10]
        return sub("cache", "export", f"{self.book_id}-{h}")

    def run(self) -> Path:
        exe = ffmpeg_exe()
        if not exe:
            raise RuntimeError("ffmpeg was not found. Install ffmpeg (winget install Gyan.FFmpeg).")
        book = self.book
        work = self._work_dir()
        total = max(1, book.unit_count())
        done = 0
        self.tts.ensure_loaded([self.tts.resolve(v) for v in self.planner.voices_used()])
        chapters: list[tuple[str, float, Path]] = []
        t0 = time.time()
        made_units = 0
        for ci, ch in enumerate(book.chapters):
            units = book.chapter_units(ci)
            if len(units) == 0:
                continue
            wav = work / f"ch_{ci:04d}.wav"
            marker = work / f"ch_{ci:04d}.done"
            if marker.exists() and wav.exists():
                chapters.append((ch.title, float(marker.read_text()), wav))
                done += len(units)
                continue
            import soundfile as sf
            frames = 0
            with sf.SoundFile(str(wav), "w", SR, 1, "PCM_16") as f:
                lead = silence(CHAPTER_LEAD_IN)         # a breath before each chapter, like a narrator
                f.write(lead)
                frames += lead.size
                for u in units:
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    audio = self.tts.synth_runs(self.planner.runs(u), self.speed)
                    audio = np.concatenate([audio, silence(self.planner.pause(u) / max(self.speed, 0.5))])
                    f.write(np.clip(audio, -1.0, 1.0))
                    frames += audio.size
                    done += 1
                    made_units += 1
                    elapsed = time.time() - t0
                    eta = elapsed / made_units * (total - done)
                    self.progress(done / total * 0.95, f"Chapter {ci + 1}/{len(book.chapters)} · {_fmt(eta)} left")
            dur = frames / SR
            marker.write_text(str(dur))
            chapters.append((ch.title, dur, wav))

        if not chapters:
            raise RuntimeError("This book has no text to narrate.")
        self.progress(0.95, "Encoding M4B…")
        listfile = work / "list.txt"
        listfile.write_text("".join("file '" + p.as_posix().replace("'", "'\\''") + "'\n" for _, _, p in chapters),
                            encoding="utf-8")
        metafile = work / "meta.txt"
        metafile.write_text(ffmeta(book.title or "Audiobook", book.author or "Unknown",
                                   [(t, d) for t, d, _ in chapters]), encoding="utf-8")
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-i", str(metafile)]
        cover_file = None
        if self.cover:
            cover_file = work / "cover.jpg"
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(self.cover)).convert("RGB")
                img.thumbnail((1400, 1400))
                img.save(cover_file, "JPEG", quality=90)
                cmd += ["-i", str(cover_file)]
            except Exception:
                cover_file = None
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover_file:
            cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        cmd += ["-c:a", "aac", "-b:a", self.bitrate, "-ac", "1", "-movflags", "+faststart", "-f", "mp4"]
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = self.out_path.with_suffix(".part.m4b")
        cmd.append(str(tmp_out))
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError("ffmpeg failed:\n" + r.stderr[-800:])
        tmp_out.replace(self.out_path)
        shutil.rmtree(work, ignore_errors=True)
        self.progress(1.0, "Done")
        return self.out_path


def _fmt(sec: float) -> str:
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec}s"
