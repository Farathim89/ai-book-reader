@echo off
rem Dev setup: creates .venv and installs PyTorch (CUDA 12.8, needed for RTX 50xx) and everything else,
rem with the same versions as the portable app (runtime\constraints.txt).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.10 -m venv .venv || goto :error
)
set PY=.venv\Scripts\python.exe
%PY% -m pip install --upgrade pip wheel || goto :error
%PY% -m pip install torch==2.11.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128 || goto :error
%PY% -m pip install -r requirements.txt -c runtime\constraints.txt || goto :error
rem chatterbox-tts pins torch==2.6 (no RTX 50xx support); its other dependencies come from requirements.txt
%PY% -m pip install chatterbox-tts==0.1.7 --no-deps || goto :error
%PY% -m pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl --no-deps || goto :error
echo.
echo Setup done. Start the app with "Ai Book Reader.cmd".
exit /b 0
:error
echo Setup failed.
exit /b 1
