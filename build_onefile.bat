@echo off
chcp 65001 >nul
setlocal

REM Build a self-contained Things Windows executable.
REM Output: dist\ThingsWindows_vX.Y.Z.exe

cd /d "%~dp0"

for /f "usebackq tokens=2 delims==\"" %%a in (`findstr /c:"VERSION = " app.py`) do set VERSION=%%a
set VERSION=%VERSION: =%
if "%VERSION%"=="" (
    echo [X] Could not read VERSION from app.py
    pause
    exit /b 1
)
set EXE_NAME=ThingsWindows_v%VERSION%

echo ============================================================
echo   Building %EXE_NAME%.exe
echo   onefile / windowed / pywebview
echo ============================================================

if exist build rmdir /s /q build
if exist dist\*.exe del /q dist\*.exe

python -m PyInstaller ^
    --onefile --windowed --noconfirm --clean ^
    --name "%EXE_NAME%" ^
    --add-data "index.html;." ^
    --add-data "vue.global.prod.js;." ^
    --add-data "tailwindcss.js;." ^
    --collect-all webview ^
    --hidden-import pythonnet ^
    --hidden-import clr ^
    --hidden-import bottle ^
    --hidden-import requests ^
    --hidden-import win32crypt ^
    main_window.py

if errorlevel 1 (
    echo.
    echo [X] Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Build complete.
echo   Output: dist\%EXE_NAME%.exe
echo ============================================================
dir dist\%EXE_NAME%.exe
echo.
pause
