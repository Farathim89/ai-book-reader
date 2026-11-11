@echo off
rem Copies the built app (dist\Ai Book Reader) into the portable folder, keeping its data\ folder
rem (library, settings, voices, AI models). Default target: ..\AiBookReader-Portable
cd /d "%~dp0"
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=%~dp0..\AiBookReader-Portable"
if not exist "dist\Ai Book Reader\Ai Book Reader.exe" (
    echo Build first: build.cmd
    exit /b 1
)
tasklist /FI "IMAGENAME eq Ai Book Reader.exe" | find /I "Ai Book Reader.exe" >nul && (
    echo Close Ai Book Reader first.
    exit /b 1
)
robocopy "dist\Ai Book Reader" "%TARGET%" /MIR /XD "dist\Ai Book Reader\data" "%TARGET%\data" /NFL /NDL /NJH /NP /R:1 /W:1
if %ERRORLEVEL% GEQ 8 (
    echo Copy failed.
    exit /b 1
)
echo Portable app: "%TARGET%\Ai Book Reader.exe"
exit /b 0
