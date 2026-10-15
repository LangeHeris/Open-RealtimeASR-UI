@echo off
cd /d "%~dp0"
setlocal

REM Build script for Open-RealtimeASR-UI.
REM Usage: build.bat [lite]   full = ships DLC theme packs; lite = no theme content.
REM NOTE: keep this file pure ASCII - non-ASCII text makes cmd mis-parse lines
REM       when combined with chcp 65001 (known cmd bug).

set "BUILD_MODE=%~1"

REM Same Python lookup order as start.bat
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
    echo Python not found. See start.bat for details.
    if not defined CI pause
    exit /b 1
)

echo [1/4] Installing PyInstaller if missing...
%PYTHON% -m pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    if not defined CI pause
    exit /b 1
)

echo [2/4] Cleaning previous build...
if exist build rmdir /s /q build

echo [3/4] Building onedir bundle from spec (no console)...
%PYTHON% scripts\gen_version_info.py
if errorlevel 1 (
    echo [ERROR] version_info generation failed.
    if not defined CI pause
    exit /b 1
)
set "ORI_BUILD_MODE=%BUILD_MODE%"
REM onedir: onefile's TEMP-dir extraction is blocked by AV heuristics
REM (bootloader: Could not create temporary directory); onedir never extracts.
REM Output: dist\ORI\ (ORI.exe + _internal\); the Release asset is a zip.
set "ORI_PACKAGE=onedir"
%PYTHON% -m PyInstaller --noconfirm openrealtimeasr-ui.spec
if errorlevel 1 (
    echo [ERROR] Build failed.
    if not defined CI pause
    exit /b 1
)

if /i "%BUILD_MODE%"=="lite" (
    echo [4/4] Lite mode: DLC theme packs excluded from this build.
    goto themes_done
)
echo [4/4] Deploying DLC theme packs to dist\ORI\themes (pixel is store-exclusive)...
%PYTHON% scripts\copy_github_themes.py dist\ORI\themes
if errorlevel 1 (
    echo [ERROR] Failed to deploy theme packs.
    if not defined CI pause
    exit /b 1
)
:themes_done

echo.
echo ============================================
echo   Build finished. Output in dist\ORI\:
echo     - ORI.exe       main program (onedir; keep _internal\ next to it)
echo     - themes\       DLC theme packs (full build only)
echo ============================================
echo.
echo Usage:
echo   1. Open dist\ORI and double-click ORI.exe.
echo      A config.yaml with full comments is auto-generated
echo      next to the exe on first run.
echo   2. Edit config.yaml to fill cloud engine keys
echo      (FunASR local engine needs no key).
echo      You can also right-click the floating bar - Settings.
echo   3. Press Ctrl+G to start voice input.
echo.
echo Notes:
echo   - Logs are written to the logs\ folder next to the exe.
echo   - The packaged exe does NOT include the FunASR local engine
echo     (funasr + torch is too large). Run from source for that.
echo   - Font: edit ui.font_file / ui.font_family in config.yaml,
echo     or right-click - Font - choose a .ttf/.otf file at runtime.
echo.
REM pause busy-loops (burns CPU) in background/CI runs: no console + stdin nul.
REM Only interactive runs need the stop.
if not defined CI pause
