@echo off
cd /d "%~dp0"
setlocal

REM Python lookup order (first hit wins):
REM   0. python.local.bat - local override (gitignored, not in repo)
REM      create it with one line: set "PYTHON=C:\path\to\python.exe"
REM      use it when Python is in a non-standard location,
REM      or when "python" in PATH is the Microsoft Store stub
REM   1. project venv: .venv\Scripts\python.exe
REM   2. py launcher (installed with the python.org installer)
REM   3. python from PATH
REM NOTE: keep this file pure ASCII - chcp 65001 + non-ASCII text
REM       makes cmd mis-parse lines (known cmd bug).

if exist "python.local.bat" call "python.local.bat"

if not defined PYTHON (
    if exist ".venv\Scripts\python.exe" set "PYTHON=%cd%\.venv\Scripts\python.exe"
)
if not defined PYTHON (
    where py >nul 2>nul && set "PYTHON=py -3"
)
if not defined PYTHON (
    where python >nul 2>nul && set "PYTHON=python"
)

if not defined PYTHON (
    echo Python not found. Fix it by either:
    echo   1. Install Python 3.10+ and check "Add to PATH"
    echo      https://www.python.org/downloads/windows/
    echo   2. Create python.local.bat in this folder with one line:
    echo      set "PYTHON=C:\path\to\python.exe"
    pause
    exit /b 1
)

set "PYTHONPATH=%cd%"
%PYTHON% run.py

echo.
echo Program exited. Press any key to close.
pause >nul
