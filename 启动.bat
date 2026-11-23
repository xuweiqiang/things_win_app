@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 优先启动 v3 打包产物，其次从 Python 源码启动原生桌面窗口。
for %%F in ("dist\ThingsWindows_v3*.exe") do (
    echo ============================================================
    echo   Things Windows 非官方客户端 v3
    echo   原生桌面窗口
    echo ============================================================
    start "" "%%~fF"
    goto :eof
)

if exist "main_window.py" (
    echo ============================================================
    echo   Things Windows 非官方客户端 v3
    echo   源码模式：原生桌面窗口
    echo ============================================================
    python main_window.py
    if errorlevel 1 (
        echo.
        echo [X] 启动失败，请确认 Python 3.10+ 与依赖已安装。
        pause
    )
    goto :eof
)

echo [X] 未找到 dist\ThingsWindows_v3*.exe 或 main_window.py
echo     请检查文件是否完整，或先运行 build_onefile.bat。
pause
