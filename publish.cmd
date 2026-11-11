@echo off
rem Releases a new version: bump __version__ in app\__init__.py and commit first.
rem Tags v<version> and pushes; GitHub Actions tests, builds and publishes the release.
rem   publish.cmd --dry-run        show what would happen
rem   publish.cmd --notes file.md  release notes from a file (default: commit subjects)
cd /d "%~dp0"
.venv\Scripts\python.exe tools\publish.py %* || goto :error
exit /b 0
:error
echo Publish failed.
exit /b 1
