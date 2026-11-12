"""Make a fast Kokoro version of a cloned voice.

Kokoro voices are voicepacks: 510 x 256 numbers (one row per sentence length) that the shared model reads with.
Kokoro has no cloning, but its voice space can be searched: start from the ready-made voices that sound closest
to the person, then try thousands of small changes and keep each one that makes a speaker-recognition model
(Chatterbox's voice encoder) hear the person more - the random-walk idea of KVoiceWalk, done with the app's
own parts. The result reads at Kokoro speed; it copies the sound of the voice, the reading style stays Kokoro's.

Measured on an audiobook narrator (held-out sentences): likeness 0.72 for the closest ready-made voice ->
0.87 after 1000 steps (an independent judge, WavLM: 0.87 -> 0.965; the cloned voice itself: 0.97)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

STEPS = 1000              # the likeness levels off by then (about 6 minutes on a good graphics card)
PACE = 0.9                # the app's natural pace (service.NATURAL_PACE)
PRACTICE = [              # what the candidates read while searching (never the book's own text)
    "The road wound down through the valley, and by evening we could see the lights of the town below.",
    "I had no idea what she meant, but I nodded anyway and tried to look like I understood.",
    "It was the kind of quiet that made you want to whisper, even when there was nobody around to hear.",
    "Of course, nothing about that plan was going to be easy. Still, it was better than doing nothing.",
    "He laughed, shook his head, and told me to come back when I had something better to offer.",
    "We spent the whole morning sorting through old boxes, looking for a map that might not even exist.",
]
WINDOW = 4.0              # seconds of the person's voice per target piece


class Cancelled(Exception):
    pass


def walk(start: np.ndarray, stock: list[np.ndarray], score, steps: int, rng: np.random.Generator,
         spread: np.ndarray, on_step=None, cancelled=lambda: False):
    """Greedy random walk from `start`. score(pack) -> (value, info); higher is better.
    A move is a random nudge of one shared 256-number offset (scaled by `spread`, so every sentence length moves
    together) or, now and then, a small pull toward one of the ready-made voices. Returns (best, value, info)."""
    best = start
    best_v, best_info = score(best)
    size, fails = 0.35, 0
    for step in range(1, steps + 1):
        if cancelled():
            raise Cancelled()
        if stock and rng.random() < 0.15:
            other = stock[int(rng.integers(len(stock)))]
            cand = best + float(rng.uniform(0.02, 0.15)) * (other - best)
        else:
            cand = best + rng.standard_normal(best.shape[-1]).astype(np.float32).reshape(1, 1, -1) * spread * size
        v, info = score(cand)
        if v > best_v:
            best, best_v, best_info, fails = cand.astype(np.float32), v, info, 0
        else:
            fails += 1
            if fails >= 60:                           # stuck: smaller steps
                size, fails = max(0.04, size * 0.7), 0
        if on_step is not None:
            on_step(step, best_v, best_info)
    return best, best_v, best_info


def _target_windows(source: str, ref_wav: str, minutes: float) -> list[np.ndarray]:
    """4-second pieces (16 kHz) of the person: spread over a long recording, or the whole short one / the clip."""
    from .clip_finder import media_duration
    from .voice_training import pick_spans
    from .voices import load_audio_any
    total = media_duration(source) if source and Path(source).exists() else 0.0
    if total and total >= 180:
        clips = [load_audio_any(source, 16000, s, d) for s, d in pick_spans(total, minutes, parts=4)]
    elif total:
        clips = [load_audio_any(source, 16000)]
    else:
        clips = [load_audio_any(ref_wav, 16000)]
    n = int(WINDOW * 16000)
    step = n if sum(len(c) for c in clips) > 60 * 16000 else n // 2
    wins = [c[k:k + n] for c in clips for k in range(0, len(c) - n + 1, step)]
    wins = [w for w in wins if np.abs(w).mean() > 0.01]
    if len(wins) < 3 and source:                      # too little from the recording: add the clip
        ref = load_audio_any(ref_wav, 16000)
        wins += [ref[k:k + n] for k in range(0, len(ref) - n + 1, n // 2)]
    return wins


def make_kokoro_voice(engine, source: str, ref_wav: str, out_path: Path, minutes: float = 8.0, steps: int = STEPS,
                      progress=None, cancelled=lambda: False, seed: int = 7, accent: str = "US") -> dict:
    """Search a voicepack for the person in `source` (falls back to the clip `ref_wav`) and save it to out_path
    (a torch .pt file Kokoro reads) with a report next to it (kokoro.json). `engine` is the app's KokoroEngine;
    accent "US" / "UK" is the pronunciation it searches (and later reads) with."""
    import librosa
    import torch

    from .clip_finder import speaker_encoder
    from .engines import offline_first
    from .voices import KOKORO_V11_VOICES, KOKORO_VOICES
    report = progress or (lambda f, m: None)
    t0 = time.time()
    rng = np.random.default_rng(seed)

    report(0.01, "Listening to the voice…")
    wins = _target_windows(source, ref_wav, minutes)
    if len(wins) < 2:
        raise ValueError("There is too little of this voice to copy (a few seconds of clear speech at least).")
    ve = speaker_encoder()
    if ve is None:
        raise ValueError("The voice encoder is missing - run Set up (Settings ▸ Repair) to download the voice models.")
    engine.load()
    device = next(engine.model.parameters()).device
    ve.to(device)
    try:
        def embed(wavs16):
            e = np.asarray(ve.embeds_from_wavs(list(wavs16), sample_rate=16000))
            return e / np.linalg.norm(e, axis=1, keepdims=True)

        E = embed(wins)
        c = E.mean(0)
        c /= np.linalg.norm(c)
        keep = (E @ c) > 0.6 if len(wins) >= 6 else np.ones(len(wins), bool)   # other speakers out
        if keep.sum() < 2:
            keep[:] = True
        target = E[keep].mean(0)
        target /= np.linalg.norm(target)
        f0 = np.concatenate([librosa.yin(w, fmin=70, fmax=500, sr=16000) for w, k in zip(wins, keep) if k])
        target_f0 = float(np.median(f0))

        report(0.08, "Getting Kokoro ready…")
        model, pipe = engine.model, engine._pipe("b" if accent == "UK" else "a")
        ps_all = [next(iter(pipe(t, voice="bf_emma" if accent == "UK" else "af_heart", speed=PACE))).phonemes
                  for t in PRACTICE]
        fit, check = ps_all[:4], ps_all[4:]

        def render(pack: np.ndarray, ps_list) -> list[np.ndarray]:
            out = []
            with torch.inference_mode():
                for ps in ps_list:
                    ref = torch.from_numpy(pack[len(ps) - 1]).to(device)
                    a = model(ps, ref, PACE).detach().float().cpu().numpy()
                    out.append(librosa.resample(a, orig_sr=24000, target_sr=16000))
            return out

        def score(pack: np.ndarray, ps_list=fit):
            wavs = render(pack, ps_list)
            sim = float((embed(wavs) @ target).mean())
            pf0 = np.concatenate([librosa.yin(w, fmin=70, fmax=500, sr=16000) for w in wavs])
            off = abs(12 * np.log2(max(1.0, float(np.median(pf0))) / target_f0))   # semitones
            return sim - 0.01 * off, {"sim": sim, "pitch": off}

        report(0.1, "Finding the closest Kokoro voices…")
        names = {vid: name for vid, name, _, _ in KOKORO_VOICES if vid not in KOKORO_V11_VOICES}
        stock = {vid: offline_first(lambda vid=vid: pipe.load_voice(vid)).float().cpu().numpy() for vid in names}
        ranked = sorted(((score(p)[1]["sim"], vid) for vid, p in stock.items()), reverse=True)
        cands = {names[ranked[0][1]]: stock[ranked[0][1]]}
        for k in (2, 3, 4):
            top = ranked[:k]
            w = np.exp((np.array([s for s, _ in top]) - top[0][0]) * 40)
            w /= w.sum()
            cands["blend of " + ", ".join(names[v] for _, v in top)] = sum(
                float(wi) * stock[v] for wi, (_, v) in zip(w, top)).astype(np.float32)
        scored = {n: score(p) for n, p in cands.items()}
        start_name = max(scored, key=lambda n: scored[n][0])
        start_sim = scored[start_name][1]["sim"]

        spread = np.stack(list(stock.values())).std(0).mean(0, keepdims=True).astype(np.float32)   # [1, 1, 256]

        def on_step(step, value, info):
            if step % 20 == 0 or step == steps:
                report(0.12 + 0.86 * step / steps,
                       f"Searching for the voice… step {step} of {steps} · likeness {info['sim'] * 100:.0f}%")

        best, _, info = walk(cands[start_name], list(stock.values()), score, steps, rng, spread, on_step, cancelled)
        check_sim = score(best, check)[1]["sim"]
    finally:
        ve.to("cpu")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + ".new.pt")
    torch.save(torch.from_numpy(np.ascontiguousarray(best)), str(tmp))
    tmp.replace(out_path)
    result = {"start_voice": start_name, "start_likeness": round(start_sim, 4),
              "closest_ready_made": names[ranked[0][1]], "likeness": round(info["sim"], 4),
              "likeness_unseen": round(check_sim, 4), "pitch_offset_semitones": round(float(info["pitch"]), 2),
              "steps": steps, "seconds": round(time.time() - t0), "date": time.strftime("%Y-%m-%d %H:%M"),
              "accent": "UK" if accent == "UK" else "US"}
    out_path.with_name("kokoro.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    report(1.0, "Done")
    return result
