from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import app
from sync_engine import SafeThings
from tests.test_sync_engine import FakeCloud, make_engine, seed_task


@contextmanager
def running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request_json(base_url, path, *, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        base_url + path,
        method=method,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def request_bytes(base_url, path):
    request = Request(base_url + path)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.headers.get_content_type(), response.read()
    except HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read()


@pytest.fixture
def api_engine(monkeypatch, tmp_path):
    cloud = FakeCloud()
    seed_task(cloud, "Seeded task")
    engine = make_engine(cloud, tmp_path)
    monkeypatch.setattr(app, "_get_things", lambda: engine)
    monkeypatch.setattr(app, "_load_pins", lambda: [])
    monkeypatch.setattr(app, "_pins_file", lambda: str(tmp_path / "pins.json"))
    with app._session_lock:
        app._session.clear()
        app._session["things"] = engine
        app._session["email"] = "test@example.com"
    yield engine, cloud
    with app._session_lock:
        app._session.clear()


def test_non_login_api_endpoints(api_engine):
    engine, _ = api_engine
    with running_server() as base:
        status, health = request_json(base, "/api/health")
        assert status == 200
        assert health["ok"] is True
        assert health["unofficial"] is True
        assert health["authenticated"] is True

        status, tasks = request_json(base, "/api/tasks/all")
        assert status == 200
        assert len(tasks["tasks"]) == 1

        status, tasks = request_json(base, "/api/tasks?list=all")
        assert status == 200
        assert tasks["tasks"][0]["title"] == "Seeded task"

        status, stats = request_json(base, "/api/stats")
        assert status == 200
        assert stats["counts"]["all"] == 1

        for path, key in (
            ("/api/tags", "tags"),
            ("/api/completed", "tasks"),
            ("/api/projects", "projects"),
            ("/api/areas", "areas"),
            ("/api/sync/status", "pending"),
            ("/api/conflicts", "conflicts"),
        ):
            status, payload = request_json(base, path)
            assert status == 200, path
            assert key in payload, path


def test_write_api_paths(api_engine):
    engine, cloud = api_engine
    with running_server() as base:
        status, created = request_json(
            base,
            "/api/tasks",
            method="POST",
            body={
                "title": "Created through API",
                "notes": "API note",
                "when": "today",
                "deadline": "2031-02-03",
                "tags": ["api"],
            },
        )
        assert status == 200
        task_uuid = created["uuid"]
        assert created["sync"]["pending"] == 0

        status, payload = request_json(
            base,
            "/api/tasks/modify",
            method="POST",
            body={
                "uuid": task_uuid,
                "fields": {
                    "tt": "Edited through API",
                    "nt": "Updated note",
                    "dd": "2031-02-04",
                },
            },
        )
        assert status == 200
        assert set(payload["changed"]) == {"title", "notes", "deadline"}

        status, payload = request_json(
            base,
            "/api/tasks/status",
            method="POST",
            body={"uuid": task_uuid, "status": "completed"},
        )
        assert status == 200
        assert payload["ok"] is True

        status, payload = request_json(
            base,
            "/api/tasks/trash",
            method="POST",
            body={"uuid": task_uuid, "trashed": True},
        )
        assert status == 200
        assert payload["ok"] is True

        task = next(
            item for item in engine.get_all_tasks() if item["uuid"] == task_uuid
        )
        assert task["title"] == "Edited through API"
        assert task["human_status"] == "trashed"
        assert cloud.commits


def test_api_rejects_unknown_modify_fields(api_engine):
    engine, _ = api_engine
    task_uuid = engine.get_all_tasks()[0]["uuid"]
    with running_server() as base:
        status, payload = request_json(
            base,
            "/api/tasks/modify",
            method="POST",
            body={"uuid": task_uuid, "fields": {"cd": 123}},
        )
        assert status == 400
        assert payload["ok"] is False


def test_index_and_local_assets_are_served(api_engine):
    with running_server() as base:
        status, content_type, body = request_bytes(base, "/")
        assert status == 200
        assert content_type == "text/html"
        assert "非官方客户端".encode("utf-8") in body
        assert b"/static/vue.global.prod.js" in body
        assert b"/static/tailwindcss.js" in body

        for path in (
            "/static/vue.global.prod.js",
            "/static/tailwindcss.js",
        ):
            status, content_type, body = request_bytes(base, path)
            assert status == 200, path
            assert content_type == "application/javascript"
            assert len(body) > 1000

        status, _, _ = request_bytes(base, "/static/../app.py")
        assert status == 404
