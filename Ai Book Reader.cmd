@echo off
rem Starts Ai Book Reader from source (run setup.cmd once first).
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0AiBookReader.pyw" %*
