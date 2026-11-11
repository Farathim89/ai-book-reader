@echo off
rem Builds the app and publishes it as a GitHub release (app update for the Update button).
rem Bump __version__ in app\__init__.py and commit first. "publish.cmd --dry-run" only makes the files.
cd /d "%~dp0"
call build.cmd || goto :error
.venv\Scripts\python.exe tools\publish.py %* || goto :error
exit /b 0
:error
echo Publish failed.
exit /b 1
