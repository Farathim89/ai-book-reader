"""Train a cloned voice on more of the person's recordings - typically the audiobook they narrated.

A 10-second clip gives Chatterbox the *sound* of a voice; their delivery (pace, rhythm, how they
act) needs more. This teaches the full Chatterbox model's text-to-speech-token part (T3, a 520M
Llama) the narrator's delivery with a small LoRA adapter (a few MB per voice; the big model is
not changed, the adapter is switched on only when that voice speaks):

  1. transcribe   Whisper (large-v3-turbo, local) writes down what is said, word by word
  2. cut          the narration is cut into sentences of 4-14 s at pauses; parts that are not the
                  main voice (intros, other speakers) are dropped with the speaker encoder
  3. tokenize     text -> text tokens, audio -> Chatterbox speech tokens (25 per second)
  4. train        LoRA on the attention of the 30 transformer layers, next-speech-token loss,
                  checked on sentences it never trained on; the best adapter is kept
Everything runs on this PC."""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

import numpy as np

WHISPER = "openai/whisper-large-v3-turbo"
TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj")
RANK, ALPHA = 16, 32
SR16 = 16000


# ------------------------------------------------------------------------------------------ LoRA
def _lora_class():
    import torch
    from torch import nn

    class LoRALinear(nn.Module):
        """A frozen Linear plus switchable low-rank adapters (one per trained voice)."""

        def __init__(self, base: nn.Linear, rank: int = RANK, alpha: int = ALPHA):
            super().__init__()
            self.base = base
            self.rank = rank
            self.scale = alpha / rank
            self.adapters: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
            self.active: str | None = None
            self.train_A: nn.Parameter | None = None
            self.train_B: nn.Parameter | None = None

        @property
        def weight(self):                      # code that peeks at .weight (dtype / device) still works
            return self.base.weight

        def start_training(self) -> None:
            dev, inp, out = self.base.weight.device, self.base.in_features, self.base.out_features
            self.train_A = nn.Parameter(torch.randn(self.rank, inp, device=dev) / math.sqrt(inp))
            self.train_B = nn.Parameter(torch.zeros(out, self.rank, device=dev))

        def stop_training(self) -> None:
            self.train_A = self.train_B = None

        def forward(self, x):
            y = self.base(x)
            if self.train_A is not None:
                return y + ((x.to(self.train_A.dtype) @ self.train_A.t()) @ self.train_B.t()).to(y.dtype) * self.scale
            pair = self.adapters.get(self.active) if self.active else None
            if pair is not None:
                a, b = pair
                y = y + ((x.to(a.dtype) @ a.t()) @ b.t()).to(y.dtype) * self.scale
            return y
    return LoRALinear


_LORA = None


def lora_modules(t3) -> list[tuple[str, object]]:
    """Wrap the attention projections of every transformer layer (once) and return them by name."""
    global _LORA
    if _LORA is None:
        _LORA = _lora_class()
    found = []
    for i, layer in enumerate(t3.tfmr.layers):
        attn = layer.self_attn
        for name in TARGETS:
            mod = getattr(attn, name)
            if not isinstance(mod, _LORA):
                mod = _LORA(mod)
                setattr(attn, name, mod)
            found.append((f"layers.{i}.self_attn.{name}", mod))
    return found


def use_adapter(t3, key: str | None, path: Path | None = None) -> None:
    """Switch the adapter of voice `key` on (loading it from `path` the first time), or all off."""
    mods = lora_modules(t3)
    if key and path is not None and key not in mods[0][1].adapters:
        from safetensors.torch import load_file
        state = load_file(str(path))
        dev = t3.device
        for name, mod in mods:
            mod.adapters[key] = (state[f"{name}.A"].to(dev), state[f"{name}.B"].to(dev))
    for _, mod in mods:
        mod.active = key if key in mod.adapters else None


def forget_adapter(t3, key: str) -> None:
    for _, mod in lora_modules(t3):
        mod.adapters.pop(key, None)
        if mod.active == key:
            mod.active = None


# ------------------------------------------------------------------------------------------ data
def pick_spans(total: float, minutes: float, parts: int = 3) -> list[tuple[float, float]]:
    """`minutes` of audio in `parts` pieces spread over the recording (skips the first 3 %)."""
    want = min(minutes * 60, total * 0.9)
    piece = want / parts
    starts = np.linspace(total * 0.03, max(total * 0.03, total - piece - 1), parts)
    return [(float(s), float(piece)) for s in starts]


def transcribe(audio16k: np.ndarray, offset: float, asr, progress=None) -> list[tuple[float, float, str]]:
    """Words with times (seconds from the start of the recording). Retries with less memory when the
    graphics card is short of it (other apps, the voice models)."""
    import torch
    out = None
    for batch in (8, 4, 1):
        try:
            out = asr({"raw": audio16k, "sampling_rate": SR16}, return_timestamps="word", chunk_length_s=30,
                      batch_size=batch, generate_kwargs={"language": "en", "task": "transcribe"})
            break
        except (torch.OutOfMemoryError, RuntimeError) as e:
            if "out of memory" not in str(e).lower() or batch == 1:
                raise
            torch.cuda.empty_cache()
    words = []
    for c in out.get("chunks", []):
        s, e = c.get("timestamp", (None, None))
        if s is None or e is None or not c.get("text", "").strip():
            continue
        words.append((offset + float(s), offset + float(e), c["text"]))
    return words


ENDS = (".", "!", "?", "…", ".\"", "!\"", "?\"", ".”", "!”", "?”")


SOFT = (",", ";", ":", "—", "-")


def utterances(words: list[tuple[float, float, str]], min_s: float = 3.5, max_s: float = 16.0
               ) -> list[tuple[float, float, str]]:
    """Cut the word stream into pieces of min_s..max_s seconds: (start, end, text). Cuts at the end of a
    sentence; a sentence longer than max_s is cut at its last comma / pause before that. (Whisper's word
    times often touch, so a cut does not need a visible gap.)"""
    out, i, n = [], 0, len(words)
    while i < n:
        start, j, soft, end_j = words[i][0], i, None, None
        while j < n:
            s, e, w = words[j]
            dur = e - start
            nxt = words[j + 1][0] if j + 1 < n else e + 0.5
            if dur >= min_s and w.strip().endswith(ENDS):
                end_j = j
                break
            if dur >= min_s and (w.strip().endswith(SOFT) or nxt - e >= 0.35):
                soft = j
            if dur > max_s:
                end_j = soft
                break
            j += 1
        if end_j is None:                       # ran out or no place to cut: skip past it
            i = j + 1
            continue
        e = words[end_j][1]
        nxt = words[end_j + 1][0] if end_j + 1 < n else e + 0.5
        text = "".join(w for _, _, w in words[i:end_j + 1]).strip()
        if len(text.split()) >= 4:
            out.append((max(0.0, start - 0.05), e + max(0.03, min(0.3, (nxt - e) / 2)), text))
        i = end_j + 1
    return out


# ------------------------------------------------------------------------------------------ training
def _batches(items: list[dict], size: int, rng: random.Random) -> list[list[dict]]:
    """Batches of similar length (less padding), in random order."""
    items = sorted(items, key=lambda d: len(d["speech"]))
    batches = [items[i:i + size] for i in range(0, len(items), size)]
    rng.shuffle(batches)
    return batches


def _collate(batch: list[dict], hp, device):
    import torch
    tl = [len(d["text"]) for d in batch]
    sl = [len(d["speech"]) for d in batch]
    text = torch.full((len(batch), max(tl)), hp.stop_text_token, dtype=torch.long)
    speech = torch.full((len(batch), max(sl)), hp.stop_speech_token, dtype=torch.long)
    for i, d in enumerate(batch):
        text[i, :tl[i]] = torch.as_tensor(d["text"])
        speech[i, :sl[i]] = torch.as_tensor(d["speech"])
    return (text.to(device), torch.tensor(tl, device=device), speech.to(device), torch.tensor(sl, device=device))


def speech_loss(t3, cond, batch: list[dict]):
    """Next-speech-token cross entropy (the delivery: which speech token comes next, given the text)."""
    import torch
    import torch.nn.functional as F
    text, tl, speech, sl = _collate(batch, t3.hp, t3.device)
    with torch.autocast("cuda", dtype=torch.bfloat16, enabled=t3.device.type == "cuda"):
        out = t3.forward(t3_cond=cond, text_tokens=text, text_token_lens=tl, speech_tokens=speech,
                         speech_token_lens=sl, training=True)
    logits = out.speech_logits[:, :-1].float()
    target = speech[:, 1:].clone()
    pos = torch.arange(target.size(1), device=target.device)[None]
    target[pos >= (sl[:, None] - 1)] = -100
    return F.cross_entropy(logits.reshape(-1, logits.size(-1)), target.reshape(-1), ignore_index=-100)


def evaluate(t3, cond, items: list[dict], size: int = 8) -> float:
    import torch
    if not items:
        return float("nan")
    total, n = 0.0, 0
    with torch.no_grad():
        for i in range(0, len(items), size):
            b = items[i:i + size]
            total += float(speech_loss(t3, cond, b)) * len(b)
            n += len(b)
    return total / n


class Cancelled(Exception):
    pass


def train_voice(model, ref_wav: str, source: str, out_dir: Path, minutes: float = 45, expressiveness: float = 0.5,
                progress=None, cancelled=lambda: False, max_steps: int = 900, batch_size: int = 8,
                lr: float = 1e-4, seed: int = 7) -> dict:
    """Train an adapter for one cloned voice. `model` is a loaded chatterbox.tts.ChatterboxTTS.
    Writes out_dir/adapter.safetensors and out_dir/training.json; returns the training report."""
    import torch
    from safetensors.torch import save_file

    from chatterbox.tts import punc_norm

    from .clip_finder import media_duration, speaker_encoder
    from .voices import load_audio_any

    report = progress or (lambda f, m: None)
    t_start = time.time()
    rng = random.Random(seed)
    torch.manual_seed(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) transcribe pieces spread over the recording
    total = media_duration(source) or 0.0
    if total < 120:
        raise ValueError("The recording is too short to train on (a few minutes at least, 30+ minutes is best).")
    spans = pick_spans(total, minutes)
    report(0.01, "Loading the speech recogniser (Whisper)…")
    torch.cuda.empty_cache()
    from transformers import pipeline
    asr = pipeline("automatic-speech-recognition", model=WHISPER, dtype=torch.float16, device=model.device)
    words, audio_parts = [], []
    for k, (s, dur) in enumerate(spans):
        if cancelled():
            raise Cancelled()
        report(0.02 + 0.28 * k / len(spans), f"Writing down what is said… part {k + 1} of {len(spans)}")
        a = load_audio_any(source, SR16, s, dur)
        audio_parts.append((s, a))
        words += transcribe(a, s, asr)
    del asr
    torch.cuda.empty_cache()

    # 2) sentences, main voice only
    report(0.31, "Cutting the narration into sentences…")
    utts = utterances(words)

    def clip(s: float, e: float) -> np.ndarray:
        for off, a in audio_parts:
            if off <= s and e <= off + len(a) / SR16:
                return a[int((s - off) * SR16): int((e - off) * SR16)]
        return np.zeros(0, np.float32)
    clips = [(s, e, t, clip(s, e)) for s, e, t in utts]
    clips = [c for c in clips if len(c[3]) > SR16 * 2]
    ve = speaker_encoder()
    kept = clips
    if ve is not None and len(clips) > 10:
        emb = np.asarray(ve.embeds_from_wavs([c[3] for c in clips], sample_rate=SR16))
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        centre = emb.mean(0)
        sims = emb @ (centre / np.linalg.norm(centre))
        kept = [c for c, sim in zip(clips, sims) if sim >= 0.55]
    if len(kept) < 40:
        raise ValueError(f"Only {len(kept)} usable sentences were found - use a longer recording of this voice.")

    # 3) tokens
    report(0.34, "Turning the sentences into speech tokens…")
    model.prepare_conditionals(ref_wav, exaggeration=expressiveness)
    cond = model.conds.t3
    t3, hp = model.t3, model.t3.hp
    items = []
    for k, (s, e, text, a) in enumerate(kept):
        if cancelled():
            raise Cancelled()
        tt = model.tokenizer.text_to_tokens(punc_norm(text))[0].tolist()
        with torch.no_grad():
            st, sl = model.s3gen.tokenizer.forward([torch.from_numpy(a).float()])
        st = st[0, : int(sl[0])].tolist()
        st = [t for t in st if t < 6561]
        items.append({"text": [hp.start_text_token] + tt + [hp.stop_text_token],
                      "speech": [hp.start_speech_token] + st + [hp.stop_speech_token], "words": text,
                      "start": s, "end": e})
    rng.shuffle(items)
    n_val = max(12, len(items) // 12)
    val, train = items[:n_val], items[n_val:]
    (out_dir / "dataset.json").write_text(json.dumps({"source": source, "val": [
        {"start": d["start"], "end": d["end"], "text": d["words"]} for d in val]}, indent=1), encoding="utf-8")

    # 4) train
    mods = lora_modules(t3)
    for _, m in mods:
        m.active = None
        m.start_training()
    params = [p for _, m in mods for p in (m.train_A, m.train_B)]
    for p in t3.parameters():
        p.requires_grad_(False)
    for p in params:
        p.requires_grad_(True)
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=0.0)
    base_val = evaluate(t3, cond, val)
    best_val, best_state, step, history = base_val, None, 0, []
    steps_per_epoch = max(1, math.ceil(len(train) / batch_size))
    max_steps = min(max_steps, steps_per_epoch * 10)
    warm = max(10, max_steps // 20)
    patience, bad = 3, 0
    try:
        while step < max_steps and bad < patience:
            for batch in _batches(train, batch_size, rng):
                if cancelled():
                    raise Cancelled()
                for g in opt.param_groups:      # warm-up, then cosine down
                    g["lr"] = lr * min(1.0, (step + 1) / warm) * (0.5 * (1 + math.cos(math.pi * step / max_steps)))
                with torch.enable_grad():
                    loss = speech_loss(t3, cond, batch)
                    loss.backward()
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % 10 == 0:
                    report(0.36 + 0.6 * step / max_steps, f"Learning the voice… step {step} of {max_steps} "
                                                          f"(loss {float(loss):.2f})")
                if step % max(25, steps_per_epoch // 2) == 0 or step >= max_steps:
                    v = evaluate(t3, cond, val)
                    history.append({"step": step, "val": round(v, 4), "train": round(float(loss), 4)})
                    if v < best_val - 1e-3:
                        best_val, bad = v, 0
                        best_state = {f"{n}.{w}": getattr(m, f"train_{w}").detach().to(torch.bfloat16).cpu()
                                      .contiguous() for n, m in mods for w in ("A", "B")}
                    else:
                        bad += 1
                if step >= max_steps or bad >= patience:
                    break
    finally:
        for _, m in mods:
            m.stop_training()
    if best_state is None:
        raise ValueError("Training did not improve on the plain voice - nothing was saved.")
    save_file(best_state, str(out_dir / "adapter.safetensors"))
    result = {"minutes": round(sum(len(a) for _, a in audio_parts) / SR16 / 60, 1), "sentences": len(items),
              "train": len(train), "val": len(val), "steps": step, "val_loss_before": round(base_val, 4),
              "val_loss_after": round(best_val, 4),
              "improvement": round(1 - math.exp(best_val - base_val), 4),     # fewer wrong next-token guesses
              "history": history, "seconds": round(time.time() - t_start), "rank": RANK, "targets": TARGETS,
              "source": source, "date": time.strftime("%Y-%m-%d %H:%M")}
    (out_dir / "training.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    report(1.0, "Done")
    return result
