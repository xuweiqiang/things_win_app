# -*- coding: utf-8 -*-
"""Local HTTP API for the unofficial Things Cloud Windows client."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

import auth_store
from sync_engine import SafeThings, SyncError, validate_outbound_items
from things_cloud import (
    THINGS_VERSION_CODE,
    ThingsCloudError,
    filter_tasks,
)

VERSION = "3.0.4"
DISPLAY_VERSION = "3.0.4"
APP_NAME = "Things Windows"
PROJECT_URL = "https://github.com/xuweiqiang/things_win_app"
DEFAULT_PORT = 55234

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else SOURCE_DIR
STATIC_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")
LOG_FILE = os.path.join(BASE_DIR, "things_debug.log")

_root_log = logging.getLogger()
if not _root_log.handlers:
    _root_log.setLevel(logging.INFO)
    _format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setFormatter(_format)
    _root_log.addHandler(_file_handler)
    if sys.stderr:
        try:
            _console_handler = logging.StreamHandler(sys.stderr)
            _console_handler.setFormatter(_format)
            _root_log.addHandler(_console_handler)
        except Exception:
            pass
logging.getLogger("urllib3").setLevel(logging.WARNING)
_log = logging.getLogger("things.app")

_session: dict = {}
_session_lock = threading.RLock()


def _appdata_dir() -> str:
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(root, "ThingsWindows")
    os.makedirs(path, exist_ok=True)
    return path


def _atomic_json_write(path: str, value) -> None:
    temp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _pins_file() -> str:
    return os.path.join(_appdata_dir(), "pins.json")


def _load_pins() -> list[str]:
    try:
        with open(_pins_file(), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return [item for item in value if isinstance(item, str)]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_pins(values: list[str]) -> None:
    uuids = []
    seen = set()
    for value in values:
        if isinstance(value, str) and value not in seen:
            uuids.append(value)
            seen.add(value)
    _atomic_json_write(_pins_file(), uuids)


def _sanitize_uuid(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value or ""))[:64]


def _sanitize_filename(value: str) -> str:
    name = os.path.basename(str(value or "")).strip()
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", name)
    return name[:120] or "未命名"


def _attachments_dir() -> str:
    path = os.path.join(_appdata_dir(), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def _attachment_path(task_uuid: str, name: str) -> str:
    folder = os.path.join(_attachments_dir(), _sanitize_uuid(task_uuid))
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, _sanitize_filename(name))


def _save_attachment(task_uuid: str, filename: str, data_b64: str) -> str:
    if not _sanitize_uuid(task_uuid):
        raise SyncError("任务 UUID 不能为空")
    name = _sanitize_filename(filename)
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception as exc:
        raise SyncError("附件数据不是有效的 Base64") from exc
    if len(raw) > 20 * 1024 * 1024:
        raise SyncError("附件超过 20MB 限制")
    target = _attachment_path(task_uuid, name)
    temp = f"{target}.tmp-{os.getpid()}-{threading.get_ident()}"
    with open(temp, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return name


def _get_things() -> SafeThings:
    with _session_lock:
        engine = _session.get("things")
        if engine is None:
            raise RuntimeError("尚未连接 Things Cloud")
        return engine


def _session_info() -> dict:
    with _session_lock:
        engine = _session.get("things")
        return {
            "authenticated": engine is not None,
            "email": _session.get("email", ""),
        }


def sync_authenticated_session() -> dict | None:
    """Synchronize the active account independently of WebView timers."""
    with _session_lock:
        engine = _session.get("things")
    if engine is None:
        return None
    return engine.sync_now()


def _open_session(email: str, password: str) -> tuple[SafeThings, str]:
    engine = SafeThings(email, password)
    with _session_lock:
        _session.clear()
        _session["things"] = engine
        _session["email"] = email
    return engine, engine.history_key


def _extract_fields(body: dict) -> dict:
    fields = body.get("fields")
    if isinstance(fields, dict):
        return fields
    return {
        key: value
        for key, value in body.items()
        if key not in ("uuid", "fields")
    }


def _project_status(task: dict) -> dict:
    return {
        **task,
        "can_edit": task.get("human_status") != "trashed",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ThingsWindows/3"

    def log_message(self, fmt, *args):
        _log.info("[HTTP] " + fmt, *args)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=400):
        self._send_json({"ok": False, "error": str(message)}, status)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise SyncError("Content-Length 非法")
        if length > 30 * 1024 * 1024:
            raise SyncError("请求体超过 30MB")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SyncError("请求 JSON 格式错误") from exc
        if not isinstance(value, dict):
            raise SyncError("请求必须是一个 JSON 对象")
        return value

    def _serve_static(self, path: str, content_type: str):
        try:
            with open(path, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            if content_type.startswith(("text/", "application/javascript")):
                content_type += "; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._error("文件不存在", 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._serve_static(INDEX_HTML, "text/html")
                return
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if not name or "/" in name or "\\" in name or name.startswith("."):
                    self._error("非法静态资源路径", 404)
                    return
                file_path = os.path.join(STATIC_DIR, name)
                content_type = {
                    ".js": "application/javascript",
                    ".css": "text/css",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".ico": "image/x-icon",
                }.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
                self._serve_static(file_path, content_type)
                return
            if path == "/api/health":
                info = _session_info()
                version_code = THINGS_VERSION_CODE
                self._send_json(
                    {
                        "ok": True,
                        "app_name": APP_NAME,
                        "version": VERSION,
                        "display_version": DISPLAY_VERSION,
                        "unofficial": True,
                        "project_url": PROJECT_URL,
                        "authenticated": info["authenticated"],
                        "compatibility_version_code": version_code,
                        "compatibility_platform": "macOS",
                    }
                )
                return
            if path == "/api/remembered":
                self._send_json({"ok": True, **auth_store.has_remembered()})
                return
            if path == "/api/pins":
                self._send_json({"ok": True, "pinned": _load_pins()})
                return
            if path == "/api/attachments/file":
                task_uuid = _sanitize_uuid((query.get("task_uuid") or [""])[0])
                name = _sanitize_filename((query.get("name") or [""])[0])
                file_path = _attachment_path(task_uuid, name)
                if not task_uuid or not os.path.isfile(file_path):
                    self._error("附件不存在", 404)
                    return
                mime = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                    ".pdf": "application/pdf",
                    ".txt": "text/plain",
                }.get(os.path.splitext(file_path)[1].lower(), "application/octet-stream")
                self._serve_static(file_path, mime)
                return
            if path == "/api/account":
                self._send_json({"ok": True, **_session_info()})
                return
            if path == "/api/tasks/all":
                tasks = _get_things().get_all_tasks()
                self._send_json({"ok": True, "tasks": tasks})
                return
            if path == "/api/tasks":
                engine = _get_things()
                list_name = (query.get("list") or ["today"])[0]
                keyword = (query.get("q") or [None])[0]
                tag_filter = (query.get("tag") or [None])[0]
                all_tasks = engine.get_all_tasks()
                if tag_filter:
                    tasks = filter_tasks(
                        all_tasks,
                        only_pending=False,
                        tag_filter=tag_filter,
                    )
                elif list_name == "logbook":
                    tasks = [
                        task
                        for task in all_tasks
                        if not task.get("trashed")
                        and task.get("human_status") in ("completed", "canceled")
                    ]
                else:
                    tasks = engine.get_tasks(list_name, keyword=keyword)
                    if keyword:
                        needle = keyword.casefold()
                        tasks = [
                            task
                            for task in tasks
                            if needle
                            in (
                                f"{task.get('title', '')} "
                                f"{task.get('notes', '')} "
                                f"{' '.join(task.get('tags') or [])}"
                            ).casefold()
                        ]
                self._send_json({"ok": True, "tasks": tasks})
                return
            if path == "/api/tags":
                self._send_json({"ok": True, "tags": _get_things().get_tags()})
                return
            if path == "/api/completed":
                tasks = [
                    task
                    for task in _get_things().get_all_tasks()
                    if not task.get("trashed")
                    and task.get("human_status") in ("completed", "canceled")
                ]
                self._send_json({"ok": True, "tasks": tasks})
                return
            if path == "/api/projects":
                tasks = [_project_status(t) for t in _get_things().get_projects()]
                self._send_json({"ok": True, "projects": tasks})
                return
            if path == "/api/areas":
                self._send_json({"ok": True, "areas": _get_things().get_areas()})
                return
            if path == "/api/stats":
                engine = _get_things()
                all_tasks = engine.get_all_tasks()
                pending = [
                    task
                    for task in all_tasks
                    if task.get("human_status") == "pending" and not task.get("trashed")
                ]
                counts = {
                    "inbox": len(filter_tasks(pending, list_name="inbox")),
                    "today": len(filter_tasks(pending, list_name="today")),
                    "upcoming": len(
                        filter_tasks(pending, list_name="upcoming")
                    ),
                    "projects": len(
                        [
                            task
                            for task in pending
                            if task.get("type") == "project"
                        ]
                    ),
                    "anytime": len(filter_tasks(pending, list_name="anytime")),
                    "someday": len(filter_tasks(pending, list_name="someday")),
                    "all": len(pending),
                    "logbook": len(
                        [
                            task
                            for task in all_tasks
                            if not task.get("trashed")
                            and task.get("human_status") in ("completed", "canceled")
                        ]
                    ),
                    "trash": len([task for task in all_tasks if task.get("trashed")]),
                }
                self._send_json({"ok": True, "counts": counts})
                return
            if path == "/api/sync/status":
                self._send_json({"ok": True, **(_get_things().sync_status())})
                return
            if path == "/api/conflicts":
                self._send_json(
                    {"ok": True, "conflicts": _get_things().get_conflicts()}
                )
                return
            if path == "/api/dedupe/plan":
                from things_cloud import build_dedupe_plan

                plan = build_dedupe_plan(_get_things().get_all_tasks())
                self._send_json({"ok": True, "plan": plan})
                return
            if path == "/api/debug/sync":
                engine = _get_things()
                from collections import Counter

                raw, head = engine.client.sync_history_items(engine.history_key, 0)
                self._send_json(
                    {
                        "ok": True,
                        "raw_count": len(raw),
                        "head_index": head,
                        "kind_dist": dict(Counter(item.get("e") for item in raw)),
                        "action_dist": {
                            str(key): value
                            for key, value in Counter(
                                item.get("t") for item in raw
                            ).items()
                        },
                        "sample_raw": raw[:10],
                    }
                )
                return
            self._error(f"未知接口: {path}", 404)
        except RuntimeError as exc:
            self._error(exc, 401)
        except (SyncError, ValueError) as exc:
            self._error(exc, 400)
        except ThingsCloudError as exc:
            self._error(exc, 502)
        except requests.RequestException as exc:
            self._error(f"Things Cloud 连接失败: {exc}", 503)
        except Exception as exc:
            _log.exception("GET %s failed", path)
            self._error(f"服务器错误: {exc}", 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/api/pins":
                _save_pins(body.get("uuids") or [])
                self._send_json({"ok": True})
                return
            if path == "/api/auth":
                email = str(body.get("email") or "").strip()
                password = body.get("password") or ""
                if not email or not password:
                    self._error("邮箱和密码不能为空")
                    return
                engine, history_key = _open_session(email, password)
                if body.get("remember"):
                    pin = str(body.get("pin") or "").strip() or None
                    try:
                        auth_store.save_credentials(email, password, pin=pin)
                    except Exception as exc:
                        _log.warning("保存凭证失败: %s", exc)
                self._send_json(
                    {
                        "ok": True,
                        "email": email,
                        "history_key": history_key,
                        "sync": engine.sync_status(),
                    }
                )
                return
            if path == "/api/auth-2fa":
                record = auth_store.load_credentials()
                if not record:
                    self._error("本机没有可用的已保存凭证")
                    return
                if record.get("2fa_enabled"):
                    pin = str(body.get("pin") or "")
                    if not auth_store.verify_2fa(pin):
                        self._error("二次验证密码错误")
                        return
                engine, history_key = _open_session(
                    record["email"], record["password"]
                )
                self._send_json(
                    {
                        "ok": True,
                        "email": record["email"],
                        "history_key": history_key,
                        "sync": engine.sync_status(),
                    }
                )
                return
            if path == "/api/remove-remembered":
                auth_store.remove_credentials()
                self._send_json({"ok": True})
                return
            if path == "/api/logout":
                with _session_lock:
                    _session.clear()
                self._send_json({"ok": True})
                return
            if path == "/api/tasks":
                engine = _get_things()
                title = body.get("title")
                task_uuid = engine.add_task(
                    title,
                    notes=body.get("notes"),
                    when=body.get("when"),
                    deadline=body.get("deadline"),
                    tags=body.get("tags") or [],
                    project_uuid=body.get("project_uuid") or None,
                    area_uuid=body.get("area_uuid") or None,
                )
                self._send_json(
                    {
                        "ok": True,
                        "uuid": task_uuid,
                        "sync": engine.sync_status(),
                    }
                )
                return
            if path == "/api/projects":
                engine = _get_things()
                project_uuid = engine.add_project(
                    body.get("title"),
                    notes=body.get("notes"),
                    deadline=body.get("deadline"),
                    tags=body.get("tags") or [],
                    area_uuid=body.get("area_uuid") or None,
                )
                self._send_json(
                    {
                        "ok": True,
                        "uuid": project_uuid,
                        "sync": engine.sync_status(),
                    }
                )
                return
            if path == "/api/tasks/status":
                engine = _get_things()
                task_uuid = str(body.get("uuid") or "")
                if not task_uuid:
                    self._error("需要任务 UUID")
                    return
                engine.set_status(task_uuid, str(body.get("status") or ""))
                self._send_json({"ok": True, "sync": engine.sync_status()})
                return
            if path == "/api/tasks/trash":
                engine = _get_things()
                task_uuid = str(body.get("uuid") or "")
                if not task_uuid:
                    self._error("需要任务 UUID")
                    return
                engine.trash_task(task_uuid, trashed=bool(body.get("trashed", True)))
                self._send_json({"ok": True, "sync": engine.sync_status()})
                return
            if path == "/api/tasks/trash-batch":
                engine = _get_things()
                uuids = body.get("uuids")
                if not isinstance(uuids, list):
                    self._error("uuids 必须是数组")
                    return
                removed = engine.trash_tasks_batch(uuids)
                self._send_json(
                    {
                        "ok": True,
                        "removed": removed,
                        "sync": engine.sync_status(),
                    }
                )
                return
            if path == "/api/tasks/move":
                engine = _get_things()
                task_uuid = str(body.get("uuid") or "")
                if not task_uuid:
                    self._error("需要任务 UUID")
                    return
                engine.move_schedule(task_uuid, str(body.get("target") or ""))
                self._send_json({"ok": True, "sync": engine.sync_status()})
                return
            if path == "/api/tasks/modify":
                engine = _get_things()
                task_uuid = str(body.get("uuid") or "")
                if not task_uuid:
                    self._error("需要任务 UUID")
                    return
                fields = _extract_fields(body)
                allowed = {
                    "tt",
                    "nt",
                    "tg",
                    "dd",
                    "when",
                    "st",
                    "sr",
                    "tir",
                    "project_uuid",
                    "area_uuid",
                }
                unknown = sorted(set(fields) - allowed)
                if unknown:
                    self._error(f"不支持修改字段: {unknown}")
                    return
                result = engine.modify_task_fields(task_uuid, fields)
                self._send_json(
                    {**result, "sync": engine.sync_status()}
                )
                return
            if path == "/api/reorder":
                engine = _get_things()
                uuids = body.get("uuids")
                if not isinstance(uuids, list):
                    self._error("uuids 必须是数组")
                    return
                count = engine.reorder_tasks(uuids)
                self._send_json(
                    {"ok": True, "reordered": count, "sync": engine.sync_status()}
                )
                return
            if path == "/api/attachments":
                name = _save_attachment(
                    body.get("task_uuid", ""),
                    body.get("filename", ""),
                    body.get("data_b64", ""),
                )
                self._send_json({"ok": True, "name": name})
                return
            if path == "/api/dedupe/apply":
                engine = _get_things()
                uuids = body.get("remove_uuids") or []
                if not isinstance(uuids, list):
                    self._error("remove_uuids 必须是数组")
                    return
                removed = engine.trash_tasks_batch(uuids)
                self._send_json(
                    {
                        "ok": True,
                        "removed": removed,
                        "sync": engine.sync_status(),
                    }
                )
                return
            if path == "/api/sync/flush":
                engine = _get_things()
                status = engine.flush()
                self._send_json({"ok": True, **status})
                return
            conflict_match = re.fullmatch(
                r"/api/conflicts/([0-9a-f]+)/(retry|abandon)", path
            )
            if conflict_match:
                engine = _get_things()
                conflict_id, action = conflict_match.groups()
                status = engine.resolve_conflict(
                    conflict_id, retry=action == "retry"
                )
                self._send_json({"ok": True, **status})
                return
            self._error(f"未知接口: {path}", 404)
        except RuntimeError as exc:
            self._error(exc, 401)
        except (SyncError, ValueError) as exc:
            self._error(exc, 400)
        except ThingsCloudError as exc:
            self._error(exc, 502)
        except requests.RequestException as exc:
            self._error(f"Things Cloud 连接失败: {exc}", 503)
        except Exception as exc:
            _log.exception("POST %s failed", path)
            self._error(f"服务器错误: {exc}", 500)


def _try_bind(preferred: int, max_tries: int = 20) -> ThreadingHTTPServer:
    last_error = None
    for offset in range(max_tries):
        port = preferred + offset
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            server.daemon_threads = True
            return server
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("无法绑定本地端口")


def main(port: int = DEFAULT_PORT, open_browser: bool = True):
    server = _try_bind(port)
    actual_port = int(server.server_address[1])
    url = f"http://127.0.0.1:{actual_port}"
    _log.info("%s v%s -> %s", APP_NAME, VERSION, url)
    print(f"{APP_NAME} v{DISPLAY_VERSION} 已启动: {url}")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    requested_port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    main(port=requested_port)
