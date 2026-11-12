import pytest

from app.tts import voice_training as VT


def test_words_are_cut_into_sentences_at_pauses():
    words, t = [], 0.0
    text = ("The rain fell all night. Anna waited by the door, listening. Nobody came. "
            "In the morning the road was gone and the river had taken the bridge with it.")
    for w in text.split():
        words.append((t, t + 0.3, " " + w))
        t += 0.35 + (0.4 if w.endswith(".") else 0.0)          # longer gaps after full stops
    utts = VT.utterances(words, min_s=2.0, max_s=8.0)
    assert utts and all(u[2].endswith(".") for u in utts)
    assert all(1.9 <= u[1] - u[0] <= 9.0 for u in utts)
    assert utts[0][2].startswith("The rain") and len(utts[0][2].split()) >= 4
    # words whose times touch (no gap) still cut at full stops; a very long sentence cuts at a comma
    touching = [(k * 0.3, k * 0.3 + 0.3, " " + w) for k, w in enumerate(
        ("One two three four five six seven eight nine ten, eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen twenty. This short tail sentence is right here now.").split())]
    cut = VT.utterances(touching, min_s=2.0, max_s=4.5)
    assert [u[2].split()[-1] for u in cut] == ["ten,", "twenty.", "now."]


def test_spans_are_spread_over_the_recording():
    spans = VT.pick_spans(10 * 3600, 30)
    assert len(spans) == 3 and abs(sum(d for _, d in spans) - 1800) < 1
    assert spans[0][0] > 0 and spans[-1][0] + spans[-1][1] <= 10 * 3600
    short = VT.pick_spans(600, 30)                               # a 10-minute file: most of it
    assert sum(d for _, d in short) <= 600 * 0.9 + 1


def test_adapters_switch_per_voice(tmp_path):
    torch = pytest.importorskip("torch")
    from safetensors.torch import save_file
    from torch import nn

    class Attn(nn.Module):
        def __init__(self):
            super().__init__()
            for n in VT.TARGETS:
                setattr(self, n, nn.Linear(8, 8))

    class Layer(nn.Module):
        def __init__(self):
            super().__init__()
            self.self_attn = Attn()

    class Tfmr(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList([Layer(), Layer()])

    class FakeT3(nn.Module):
        def __init__(self):
            super().__init__()
            self.tfmr = Tfmr()

        @property
        def device(self):
            return torch.device("cpu")

    t3 = FakeT3()
    x = torch.randn(3, 8)
    q = t3.tfmr.layers[0].self_attn.q_proj
    plain = q(x).detach()
    mods = VT.lora_modules(t3)
    assert len(mods) == 2 * len(VT.TARGETS) and VT.lora_modules(t3)[0][1] is mods[0][1]   # wrapped once
    wrapped = t3.tfmr.layers[0].self_attn.q_proj
    assert torch.allclose(wrapped(x), plain)                       # no adapter: unchanged
    state = {f"{n}.{w}": (torch.ones(VT.RANK, 8) * 0.1 if w == "A" else torch.ones(8, VT.RANK) * 0.1)
             for n, _ in mods for w in ("A", "B")}
    save_file(state, str(tmp_path / "a.safetensors"))
    VT.use_adapter(t3, "clone:x", tmp_path / "a.safetensors")
    assert not torch.allclose(wrapped(x), plain)                   # voice x: adapter on
    VT.use_adapter(t3, None)
    assert torch.allclose(wrapped(x), plain)                       # another voice: off again
    VT.use_adapter(t3, "clone:x", tmp_path / "a.safetensors")
    VT.forget_adapter(t3, "clone:x")
    assert torch.allclose(wrapped(x), plain) and "clone:x" not in wrapped.adapters
