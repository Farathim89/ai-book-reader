@echo off
rem Builds the small portable app and puts it into the portable folder, keeping its data\ folder
rem (library, settings, voices, models) and the AI parts installed in its python\ folder.
rem Default target: ..\AiBookReader-Portable   (a new folder gets the Set up window on first start)
cd /d "%~dp0"
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=%~dp0..\AiBookReader-Portable"
.venv\Scripts\python.exe tools\build_lite.py --no-zip --install "%TARGET%" || goto :error
echo Portable app: "%TARGET%\Ai Book Reader.exe"
exit /b 0
:error
echo Failed.
exit /b 1
