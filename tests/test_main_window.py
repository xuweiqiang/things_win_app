from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import main_window


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self):
        return [handler() for handler in self.handlers]


def test_second_instance_activates_existing_window_and_returns(monkeypatch):
    activated = []
    monkeypatch.setattr(main_window, "acquire_single_instance", lambda: None)
    monkeypatch.setattr(
        main_window,
        "activate_existing_window",
        lambda: activated.append(True) or True,
    )
    monkeypatch.setattr(
        main_window,
        "start_backend_thread",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("second instance must not start another backend")
        ),
    )

    assert main_window.main() is None
    assert activated == [True]


def test_status_printing_survives_windowed_code_page_errors(monkeypatch):
    def broken_print(*args, **kwargs):
        raise UnicodeEncodeError("gbk", "ok", 0, 1, "illegal multibyte sequence")

    monkeypatch.setattr("builtins.print", broken_print)

    main_window.print_status("status")


def test_main_keeps_app_running_when_window_is_closed(monkeypatch):
    events = []
    window = SimpleNamespace(
        events=SimpleNamespace(closing=FakeEvent()),
        minimize=lambda: events.append("minimize"),
    )
    create_kwargs = {}

    class FakeStopEvent:
        def __init__(self):
            self.was_set = False

        def set(self):
            self.was_set = True

    stop_sync = FakeStopEvent()

    class FakeSyncThread:
        def __init__(self):
            self.join_args = None

        def join(self, timeout=None):
            self.join_args = (timeout,)

    sync_thread = FakeSyncThread()

    class FakeServer:
        def __init__(self):
            self.shutdown_called = False
            self.close_called = False

        def shutdown(self):
            self.shutdown_called = True

        def server_close(self):
            self.close_called = True

    server = FakeServer()
    handle = object()

    def create_window(**kwargs):
        create_kwargs.update(kwargs)
        return window

    fake_webview = SimpleNamespace(
        create_window=create_window,
        start=lambda debug=False: events.append(("start", debug)),
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(main_window, "acquire_single_instance", lambda: handle)
    released = []
    monkeypatch.setattr(
        main_window,
        "release_single_instance",
        lambda value: released.append(value),
    )
    monkeypatch.setattr(
        main_window,
        "start_backend_thread",
        lambda port: (12345, server),
    )
    monkeypatch.setattr(main_window, "wait_for_backend", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        main_window,
        "start_background_sync_thread",
        lambda: (stop_sync, sync_thread),
    )

    main_window.main()

    assert create_kwargs["confirm_close"] is False
    assert window.events.closing.emit() == [False]
    assert "minimize" in events
    assert ("start", False) in events
    assert stop_sync.was_set is True
    assert sync_thread.join_args == (2,)
    assert server.shutdown_called is True
    assert server.close_called is True
    assert released == [handle]


def test_wait_for_backend_requires_the_expected_version(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    monkeypatch.setattr(
        main_window.urllib.request,
        "urlopen",
        lambda *args, **kwargs: Response({"ok": True, "version": "3.0.3"}),
    )
    assert main_window.wait_for_backend(
        12345, timeout=0.1, expected_version="3.0.3"
    )

    clock = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(main_window.time, "time", lambda: next(clock))
    monkeypatch.setattr(main_window.time, "sleep", lambda _value: None)
    monkeypatch.setattr(
        main_window.urllib.request,
        "urlopen",
        lambda *args, **kwargs: Response({"ok": True, "version": "3.0.2"}),
    )
    assert not main_window.wait_for_backend(
        12345, timeout=0.5, expected_version="3.0.3"
    )
