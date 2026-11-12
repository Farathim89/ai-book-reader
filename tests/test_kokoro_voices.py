import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

from app.tts import kokoro_search as KS  # noqa: E402
from app.tts import voices as V  # noqa: E402


def test_walk_moves_toward_what_scores_better():
    rng = np.random.default_rng(1)
    target = rng.standard_normal(256).astype(np.float32)
    start = np.zeros((510, 1, 256), np.float32)
    stock = [rng.standard_normal((510, 1, 256)).astype(np.float32) for _ in range(5)]

    def score(pack):
        d = float(np.linalg.norm(pack.mean(0)[0] - target))
        return -d, {"sim": -d}
    spread = np.ones((1, 1, 256), np.float32) * 0.3
    seen = []
    best, value, info = KS.walk(start, stock, score, 400, rng, spread, on_step=lambda s, v, i: seen.append(v))
    assert value > score(start)[0] + 3                       # clearly closer than where it started
    assert seen == sorted(seen)                              # only ever keeps improvements
    assert best.shape == start.shape and best.dtype == np.float32
    with pytest.raises(KS.Cancelled):
        KS.walk(start, stock, score, 10, rng, spread, cancelled=lambda: True)


def test_target_windows_from_a_short_recording(tmp_path):
    sr = 24000
    t = np.arange(20 * sr) / sr
    p = tmp_path / "clip.wav"
    sf.write(str(p), (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), sr)
    wins = KS._target_windows(str(p), str(p), 8)
    assert len(wins) >= 8 and all(len(w) == 4 * 16000 for w in wins)     # 4 s pieces, overlapping (short)
    assert KS._target_windows(str(tmp_path / "gone.m4b"), str(p), 8)      # source moved away: the clip


def test_fast_voice_is_listed_routed_and_removed(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    from app.tts.service import TTSService
    sr = 24000
    rec = tmp_path / "rec.wav"
    sf.write(str(rec), (0.3 * np.sin(2 * np.pi * 220 * np.arange(12 * sr) / sr)).astype(np.float32), sr)
    clone = V.add_clone("Narrator", "female", str(rec), seconds=10)
    slug = clone.id.split(":", 1)[1]
    tts = TTSService()
    assert V.fast_pack(clone.id) is None and not V.fast_voices()
    assert tts.resolve(f"fast:{slug}") == V.DEFAULT_VOICE              # none made yet
    pack = V.clones_dir() / slug / "kokoro.pt"
    torch.save(torch.zeros(510, 1, 256), str(pack))
    fast = V.find_voice(f"fast:{slug}")
    assert (fast.id, fast.name, fast.engine, fast.gender) == (f"fast:{slug}", "Narrator (fast)", "kokoro", "female")
    assert V.kokoro_arg(fast.id) == str(pack) and V.kokoro_arg("kokoro:af_bella") == "af_bella"
    assert tts.resolve(fast.id) == fast.id and tts.engine_for(fast.id) is tts.kokoro
    key = tts._gain_key(fast.id)
    import os
    os.utime(pack, (pack.stat().st_atime, pack.stat().st_mtime + 5))     # made again: measured again
    assert tts._gain_key(fast.id) != key
    # pronunciation: American by default, British on request (no new search needed)
    assert V.fast_accent(fast.id) == "US" and V.kokoro_lang(fast.id) == "a"
    key = tts._gain_key(fast.id)
    V.set_fast_accent(fast.id, "UK")
    assert V.kokoro_lang(fast.id) == "b" and V.find_voice(fast.id).detail.startswith("UK,")
    assert tts._gain_key(fast.id) != key
    assert V.kokoro_lang("kokoro:bf_emma") == "b" and V.kokoro_lang("kokoro:af_heart") == "a"
    V.remove_fast(fast.id)
    assert V.fast_pack(clone.id) is None and tts.resolve(fast.id) == V.DEFAULT_VOICE
    assert V.find_voice(clone.id).id == clone.id                         # the cloned voice stays


def test_kokoro_11_voices_are_listed_but_not_auto_cast():
    from app.tts.planner import FEMALE_POOL, MALE_POOL
    labels = {v.id: v.detail for v in V.kokoro_voices()}
    for vid in ("af_maple", "af_sol", "bf_vale"):
        assert labels[f"kokoro:{vid}"].endswith("Kokoro 1.1")
        assert f"kokoro:{vid}" not in FEMALE_POOL + MALE_POOL
    assert "kokoro:af_heart" in FEMALE_POOL and labels["kokoro:af_heart"] == "US female"


def test_kokoro_engine_picks_the_model_and_accent():
    pytest.importorskip("torch")
    from app.tts.engines import KokoroEngine
    used = []

    class FakePipe:
        def __init__(self, tag):
            self.tag, self.voices = tag, {}

        def load_voice(self, voice):
            self.voices[voice] = object()

        def __call__(self, text, voice, speed, split_pattern=None):
            used.append((self.tag, voice))
            return []
    e = KokoroEngine()
    e._pipe = lambda lang: FakePipe(f"1.0-{lang}")
    e._pipe11 = lambda lang: FakePipe(f"1.1-{lang}")
    for voice in ("af_heart", "bf_emma", "af_maple", "bf_vale", r"D:\data\voices\x\kokoro.pt"):
        e.synth("Hello there.", voice, 1.0)
    e.synth("Hello there.", r"D:\data\voices\x\kokoro.pt", 1.0, lang="b")      # a British fast voice
    assert [t for t, _ in used] == ["1.0-a", "1.0-b", "1.1-a", "1.1-b", "1.0-a", "1.0-b"]
