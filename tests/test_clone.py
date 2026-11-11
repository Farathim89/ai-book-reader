import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

from app.tts import voices as V  # noqa: E402


@pytest.fixture
def long_recording(tmp_path):
    """60 s: a 220 Hz tone for the first 30 s, then 440 Hz."""
    sr = 24000
    t = np.arange(30 * sr) / sr
    audio = np.concatenate([0.3 * np.sin(2 * np.pi * 220 * t), 0.3 * np.sin(2 * np.pi * 440 * t)])
    p = tmp_path / "long.wav"
    sf.write(str(p), audio.astype(np.float32), sr)
    return p


def _pitch(a: np.ndarray, sr: int = 24000) -> float:
    spec = np.abs(np.fft.rfft(a))
    return float(np.argmax(spec) * sr / len(a))


def test_only_the_chosen_part_is_read(long_recording):
    part = V.load_audio_any(str(long_recording), 24000, start=35, seconds=10)
    assert abs(len(part) / 24000 - 10) < 0.1
    assert abs(_pitch(part) - 440) < 5                  # from the second half
    assert len(V.load_audio_any(str(long_recording), 24000, start=999, seconds=10)) == 0


def test_clone_quality_settings_and_engine_choice(long_recording, tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    from app.tts.engines import ChatterboxEngine, ChatterboxFullEngine, cfg_for
    from app.tts.service import TTSService
    v = V.add_clone("Narrator", "female", str(long_recording), start=0, seconds=12)
    assert V.clone_settings(v.id) == {"model": "full", "expressiveness": 0.5}     # best quality by default
    tts = TTSService()
    assert isinstance(tts.engine_for(v.id), ChatterboxFullEngine)
    key_full = tts._gain_key(v.id)
    V.set_clone_settings(v.id, model="turbo", expressiveness=3)                   # out of range: clamped
    assert V.clone_settings(v.id) == {"model": "turbo", "expressiveness": 1.0}
    assert isinstance(tts.engine_for(v.id), ChatterboxEngine)
    assert tts._gain_key(v.id) != key_full                                         # loudness measured again
    assert isinstance(tts.engine_for("chatterbox:default"), ChatterboxEngine)    # the built-in voice stays fast
    assert cfg_for(0.5) == 0.5 and cfg_for(0.9) == pytest.approx(0.3) and cfg_for(0.25) == 0.5
    old = V.clones_dir() / v.id.split(":")[1] / "voice.json"                     # clones made before 1.2.4
    old.write_text('{"name": "Narrator", "gender": "female"}', encoding="utf-8")
    assert V.clone_settings(v.id)["model"] == "full"


def test_progress_is_reported_while_reading(long_recording, tmp_path, monkeypatch):
    if not V.ffmpeg_exe():
        pytest.skip("needs ffmpeg")
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    steps = []
    V.add_clone("Progress", "male", str(long_recording), start=10, seconds=15,
                progress=lambda f, msg: steps.append((f, msg)))
    fractions = [f for f, _ in steps]
    assert fractions[0] == 0.0 and fractions[-1] == 1.0 and fractions == sorted(fractions)
    assert any(msg.startswith("Reading") for _, msg in steps) and steps[-1][1] == "Done"


def test_add_clone_cuts_the_clip_and_explains_bad_starts(long_recording, tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    v = V.add_clone("Test Voice", "female", str(long_recording), start=5, seconds=20)
    ref = V.clones_dir() / v.id.split(":")[1] / "ref.wav"
    info = sf.info(str(ref))
    assert abs(info.duration - 20) < 0.1 and info.samplerate == 24000
    with pytest.raises(ValueError, match="Start at"):
        V.add_clone("Too far", "female", str(long_recording), start=58, seconds=20)
