@echo off
rem Builds the portable app folder dist\Ai Book Reader\ (run setup.cmd once first).
rem The folder is big (~5 GB, mostly PyTorch + CUDA). AI models download into its data\ folder on first use.
rem The app's data\ folder (library, cloned voices, models) is kept when rebuilding.
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install -q pyinstaller || goto :error
if exist "dist\Ai Book Reader\data" (
    if exist "dist\_data_keep" goto :keep_exists
    move "dist\Ai Book Reader\data" "dist\_data_keep" >nul || goto :error
)
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean AiBookReader.spec
set BUILD_RC=%ERRORLEVEL%
if exist "dist\_data_keep" move "dist\_data_keep" "dist\Ai Book Reader\data" >nul
if not "%BUILD_RC%"=="0" goto :error
echo.
echo Built: "%~dp0dist\Ai Book Reader\Ai Book Reader.exe"
exit /b 0
:keep_exists
echo dist\_data_keep already exists (left over from a failed build). Move it back into "dist\Ai Book Reader\data" first.
exit /b 1
:error
echo Build failed.
exit /b 1
