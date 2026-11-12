import numpy as np
import pytest

from app.audio.export import ExportJob
from app.formats.model import Book, Chapter, Paragraph, Quote
from app.formats.textproc import find_quotes, split_sentences
from app.tts.planner import VoicePlanner


def _book(paragraphs: list[str]) -> Book:
    from app.formats.model import Sentence
    paras = []
    for text in paragraphs:
        p = Paragraph(text)
        p.sentences = [Sentence(s, e) for s, e in split_sentences(text)]
        p.quotes = [Quote(s, e) for s, e in find_quotes(text)]
        paras.append(p)
    b = Book(path="x.txt", fmt="txt", title="T", author="A", chapters=[Chapter("One", paras)])
    b.build_index()
    return b


class FakeEngine:
    max_chars = 120


class FakeTTS:
    def __init__(self):
        self.calls = []

    def resolve(self, v):
        return v

    def engine_for(self, v):
        return FakeEngine()

    def ensure_loaded(self, voices):
        pass

    def synth(self, voice, text, speed=1.0):
        self.calls.append((voice, text))
        return np.full(2400, 0.1, np.float32)

    def synth_runs(self, runs, speed=1.0):
        self.calls.append(tuple(runs))
        return np.full(2400, 0.1, np.float32)


def test_paragraph_sentences_are_read_together():
    b = _book(["One. Two. Three is a bit longer.",
               "“Hi,” said Ann. Then she left. And the door shut.",
               "A new paragraph."])
    b.chapters[0].paragraphs[1].quotes[0].speaker = "Ann"
    planner = VoicePlanner(b, "N", {"Ann": "A"}, use_characters=True)
    job = ExportJob(1, b, planner, FakeTTS(), "out.m4b")
    seg = job.segments(range(b.unit_count()))
    shape = [(s["voice"], len(s["units"])) for s in seg]
    # paragraph 1: three narrator sentences together; paragraph 2: the mixed sentence alone, then the
    # two narrator sentences together; paragraph 3 alone (paragraphs are never joined)
    assert shape == [("N", 3), (None, 1), ("N", 2), ("N", 1)]
    assert seg[0]["texts"] == ["One.", "Two.", "Three is a bit longer."]
    assert sum(len(s["units"]) for s in seg) == b.unit_count()


def test_long_paragraphs_split_at_the_model_limit_and_can_be_turned_off():
    long = " ".join(f"Sentence number {i} is here." for i in range(12))      # ~330 chars
    b = _book([long])
    planner = VoicePlanner(b, "N", {})
    seg = ExportJob(1, b, planner, FakeTTS(), "o.m4b").segments(range(b.unit_count()))
    assert all(len(" ".join(s["texts"])) <= FakeEngine.max_chars for s in seg) and len(seg) > 1
    off = ExportJob(1, b, planner, FakeTTS(), "o.m4b", paragraphs=False).segments(range(b.unit_count()))
    assert len(off) == b.unit_count()


def test_paragraph_mode_is_part_of_the_export_cache_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path))
    b = _book(["One. Two."])
    planner = VoicePlanner(b, "N", {})
    assert ExportJob(1, b, planner, FakeTTS(), "o").\
        _work_dir() != ExportJob(1, b, planner, FakeTTS(), "o", paragraphs=False)._work_dir()
