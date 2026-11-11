# PyInstaller spec - portable folder build: dist\Ai Book Reader\Ai Book Reader.exe
# AI models are not bundled; they download into dist\Ai Book Reader\data\models on first use.
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [("assets", "assets")]
binaries = []
hiddenimports = ["app.main"]

for pkg in ("kokoro", "misaki", "spacy", "thinc", "en_core_web_sm", "chatterbox", "perth", "s3tokenizer",
            "rapidocr_onnxruntime", "espeakng_loader", "phonemizer", "language_tags", "segments", "csvw",
            "wordninja", "imageio_ffmpeg", "librosa", "conformer", "diffusers", "pyloudnorm",
            "num2words", "curated_tokenizers", "curated_transformers", "spacy_curated_transformers",
            "spacy_legacy", "spacy_loggers", "srsly", "blis", "cymem", "preshed", "murmurhash", "omegaconf",
            "mobi", "striprtf", "pymupdf", "fitz", "docx"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # package missing - skip
        print("collect_all skipped", pkg, e)

for meta in ("torch", "torchaudio", "transformers", "tokenizers", "huggingface_hub", "safetensors", "numpy",
             "tqdm", "regex", "requests", "packaging", "filelock", "pyyaml", "spacy", "thinc", "kokoro", "misaki",
             "chatterbox-tts", "librosa", "diffusers", "accelerate", "onnxruntime", "en_core_web_sm", "scipy"):
    try:
        datas += copy_metadata(meta)
    except Exception:
        pass

a = Analysis(
    ["AiBookReader.pyw"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "gradio", "matplotlib", "IPython", "jupyter", "notebook", "tensorboard",
              "torch.utils.tensorboard", "pre_commit", "virtualenv"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ai Book Reader", icon="assets/icon.ico",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Ai Book Reader", upx=False)
