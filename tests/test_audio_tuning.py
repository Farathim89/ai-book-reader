import json

import numpy as np

from app.config import DEFAULTS, Settings
from app.tts import service as svc
from app.tts.engines import SR


def tone(seconds: float, amp: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_old_default_pauses_move_to_the_new_ones(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"pause_sentence": 0.18, "pause_paragraph": 0.7, "pause_heading": 0.8,
                             "pause_chapter": 1.5, "speed": 1.0}), encoding="utf-8")
    s = Settings(p)
    assert s["pause_sentence"] == DEFAULTS["pause_sentence"] == 0.45      # untouched old default -> new
    assert s["pause_paragraph"] == 0.7                                      # the user's own value stays
    assert s["pause_chapter"] == DEFAULTS["pause_chapter"]
    assert s["settings_version"] == DEFAULTS["settings_version"]
    s.save()
    again = Settings(p)                                                      # migrates only once
    again["pause_sentence"] = 0.18
    again.save()
    assert Settings(p)["pause_sentence"] == 0.18


def test_limiter_keeps_peaks_under_the_ceiling():
    a = tone(1.0, 0.5)
    a[5000:5010] = 1.6
    out = svc.limit(a)
    assert np.abs(out).max() <= svc.CEILING + 1e-3
    assert np.allclose(out[12000:], a[12000:])            # far from the peak nothing changes
    quiet = tone(0.5, 0.3)
    assert svc.limit(quiet) is quiet                       # nothing to do


def test_loudness_rises_6db_for_double_amplitude():
    a, b = svc.loudness(tone(2.0, 0.1)), svc.loudness(tone(2.0, 0.2))
    assert a is not None and abs((b - a) - 6.02) < 0.3
    assert svc.loudness(tone(0.2, 0.1)) is None             # too short to measure


def test_voices_are_brought_to_the_same_loudness(tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path))
    tts = svc.TTSService()
    level = {"kokoro:af_heart": 0.05, "kokoro:am_adam": 0.2}
    calls = []

    def raw(voice_id, text, speed):
        calls.append((voice_id, round(speed, 3)))
        return tone(3.0, level[voice_id])
    monkeypatch.setattr(tts, "_synth_raw", raw)
    quiet = tts.synth("kokoro:af_heart", "Hello there.")
    loud = tts.synth("kokoro:am_adam", "Hello there.")
    lq, ll = svc.loudness(quiet), svc.loudness(loud)
    assert abs(lq - ll) < 0.5 and abs(lq - svc.TARGET_LUFS) < 1.5
    assert ("kokoro:af_heart", svc.NATURAL_PACE) in calls       # 1.0x speaks at the natural pace
    saved = json.loads((tmp_path / "voices" / "loudness.json").read_text())
    assert set(saved) == {"kokoro:af_heart", "kokoro:am_adam"}
    n = len(calls)
    tts.synth("kokoro:af_heart", "Again.")
    assert len(calls) == n + 1                                    # measured once, then remembered


def test_pending_update_installs_once(tmp_path, monkeypatch):
    import hashlib
    import io
    import zipfile

    from app import updater
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABR_VERSION_OVERRIDE", "1.2.1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(updater.EXE_NAME, b"NEW")
        z.writestr("app/main.py", b"main")
    zp = tmp_path / "AiBookReader-src-1.2.2.zip"
    zp.write_bytes(buf.getvalue())
    rel = updater.Release("1.2.2", "v1.2.2", "", "", "", zp.name, zp.stat().st_size,
                          hashlib.sha256(zp.read_bytes()).hexdigest(), "")
    target = tmp_path / "portable"
    target.mkdir()
    monkeypatch.setattr(updater, "app_dir", lambda: target)
    monkeypatch.setattr(updater, "can_install", lambda: True)
    updater.save_pending(rel, zp)
    assert updater.pending()["version"] == "1.2.2"
    assert updater.install_pending() == target / updater.EXE_NAME
    assert (target / "app" / "main.py").read_bytes() == b"main"
    assert updater.pending() is None and updater.install_pending() is None
    # a pending update that is not newer than the app is dropped
    updater.save_pending(updater.Release("1.2.1", "", "", "", "", "", 0, "", ""), zp)
    assert updater.pending() is None
    # a damaged download is not installed
    updater.save_pending(rel, zp)
    zp.write_bytes(b"broken")
    assert updater.install_pending() is None and not zp.exists()
