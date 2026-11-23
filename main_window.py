# -*- coding: utf-8 -*-
"""
Things Windows 非官方客户端 —— 原生桌面窗口启动器
====================================================
用 pywebview (Windows WebView2) 把 Things Web 界面包成原生窗口。

架构:
  同一进程内:
    1. 导入 app 模块（后端 HTTP server，默认端口 55234）
    2. 在守护线程里启动后端（serve_forever）
    3. 用 pywebview 创建原生窗口并指向本地后端
    4. 关闭窗口时最小化到任务栏，后台同步继续运行

运行:
  python main_window.py

打包:
  双击 build_onefile.bat，或使用 README 中的 PyInstaller 命令。
"""

import ctypes
import json
import os
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

# ---------- PyInstaller 兼容 ----------
if getattr(sys, 'frozen', False):
    # 打包后资源位于 sys._MEIPASS，运行数据和日志位于 EXE 同级目录。
    BASE_DIR = os.path.dirname(sys.executable)
    sys.path.insert(0, sys._MEIPASS)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, BASE_DIR)

BACKEND_PORT = 55234
SINGLE_INSTANCE_MUTEX = r"Local\ThingsWindows.SingleInstance"
ERROR_ALREADY_EXISTS = 183
SW_SHOW = 5
SW_RESTORE = 9


def print_status(message: str) -> None:
    """Best-effort console output for both console and windowed bootloaders."""
    try:
        print(message, flush=True)
    except (AttributeError, OSError, UnicodeEncodeError):
        pass


def acquire_single_instance(name: str = SINGLE_INSTANCE_MUTEX):
    """Acquire a process-lifetime named mutex or return ``None``."""
    if os.name != "nt":
        raise RuntimeError("Things Windows requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle) -> None:
    if not handle or os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def activate_existing_window() -> bool:
    """Restore the running Things Windows window from another process."""
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    matches = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        if buffer.value.startswith("Things Windows "):
            matches.append(hwnd)
            return False
        return True

    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    if not matches:
        return False
    hwnd = matches[0]
    command = SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW
    user32.ShowWindow(hwnd, command)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def start_backend_thread(port: int = BACKEND_PORT):
    """Bind once and return ``(actual_port, server)``."""
    import app as webapp  # 延迟导入，确保 sys.path 已设置

    server = webapp._try_bind(port)
    actual_port = int(server.server_address[1])
    webapp.DEFAULT_PORT = actual_port

    def _run():
        try:
            if actual_port != port:
                print_status(
                    f"[backend] port {port} is busy; using {actual_port}"
                )
            print_status(
                f"[backend] HTTP server listening on http://localhost:{actual_port}"
            )
            server.serve_forever()
        except Exception as e:
            print_status(f"[backend] startup failed: {e}")

    t = threading.Thread(target=_run, daemon=True, name="ThingsBackend")
    t.start()
    return actual_port, server


def wait_for_backend(
    port: int,
    timeout: float = 10.0,
    expected_version: str | None = None,
) -> bool:
    """Wait for this exact backend and verify its application version."""
    if expected_version is None:
        import app as webapp

        expected_version = webapp.VERSION
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1
            ) as response:
                health = json.loads(response.read().decode("utf-8"))
            if health.get("ok") is True and health.get("version") == expected_version:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def start_background_sync_thread(interval: float = 15.0):
    """Keep pulling Cloud changes even when WebView is minimized."""
    import app as webapp

    stop_event = threading.Event()

    def _run():
        while not stop_event.wait(interval):
            try:
                webapp.sync_authenticated_session()
            except Exception as exc:
                print_status(f"[sync] background sync failed: {exc}")

    thread = threading.Thread(
        target=_run,
        daemon=True,
        name="ThingsBackgroundSync",
    )
    thread.start()
    return stop_event, thread


def minimize_on_close(window) -> bool:
    window.minimize()
    return False


def attach_close_handler(window) -> None:
    window.events.closing += lambda: minimize_on_close(window)


def main():
    mutex = acquire_single_instance()
    if mutex is None:
        activate_existing_window()
        return

    import app as webapp

    try:
        import webview

        print_status("=" * 50)
        print_status(
            f"  Things Windows v{webapp.DISPLAY_VERSION} unofficial client"
        )
        print_status("=" * 50)

        actual_port, server = start_backend_thread(BACKEND_PORT)
        if not wait_for_backend(
            actual_port,
            timeout=15,
            expected_version=webapp.VERSION,
        ):
            server.shutdown()
            server.server_close()
            print_status("[main] backend failed to start or version mismatch")
            return

        stop_sync, sync_thread = start_background_sync_thread()
        url = f"http://localhost:{actual_port}/?v={webapp.VERSION}"
        print_status(f"[main] backend ready: {url}")
        print_status("[main] opening native window")

        window = webview.create_window(
            title=f"Things Windows v{webapp.DISPLAY_VERSION} · 非官方客户端",
            url=url,
            width=1280,
            height=800,
            min_size=(800, 600),
            text_select=True,
            confirm_close=False,
            background_color="#f5f5f7",
        )
        attach_close_handler(window)

        webview.start(debug=False)
        stop_sync.set()
        sync_thread.join(timeout=2)
        server.shutdown()
        server.server_close()
    finally:
        release_single_instance(mutex)


if __name__ == "__main__":
    main()
