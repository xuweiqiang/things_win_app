# -*- coding: utf-8 -*-
"""Durable Things Cloud sync engine with a safe write boundary.

The public Cloud protocol is unofficial. This module keeps all Task/Project
writes in one place, validates the payload before it reaches the network, keeps
a disk cache for offline reads, and persists a write-ahead operation queue.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import time
import uuid as uuid_mod
import zlib
from datetime import datetime, timezone

import requests

from things_cloud import (
    ACTION_CREATED,
    ACTION_MODIFIED,
    KIND_AREA,
    KIND_TAG,
    SS_CANCELED,
    SS_COMPLETED,
    SS_PENDING,
    ST_ANYTIME,
    ST_INBOX,
    ST_SOMEDAY,
    ThingsClient,
    ThingsCloudError,
    ThingsConflictError,
    _date_to_ts,
    _rebuild_state,
    _resolve_when,
    _things_now,
    filter_tasks,
    new_uuid,
    notes_to_cloud,
    validate_uuid,
)

_log = logging.getLogger("things.sync")

TASK7_CREATE_KEYS = {
    "tp", "sr", "dds", "rt", "rmd", "ss", "tr", "dl", "icp", "st", "ar",
    "tt", "do", "lai", "tir", "tg", "agr", "ix", "cd", "lt", "icc", "md",
    "ti", "dd", "ato", "nt", "icsd", "pr", "rp", "acrd", "sp", "sb", "rr",
    "xx",
}

SAFE_TASK7_SCHEDULE_KEYS = {"st", "sr", "tir"}
SAFE_TASK7_STATUS_KEYS = {"ss", "sp"}

TASK7_MODIFY_KEYS = {
    "md", "tt", "nt", "st", "sr", "tir", "dd", "ar", "pr", "agr", "tg",
    "ss", "sp", "tr", "ix", "ti", "do",
}


class SyncError(ValueError):
    """User-actionable local validation or synchronization error."""


class LocalStore:
    """Small atomic JSON store; no database dependency is required."""

    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def read(self, name: str, default):
        path = os.path.join(self.directory, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return copy.deepcopy(default)

    def write(self, name: str, value) -> None:
        path = os.path.join(self.directory, name)
        temp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)


def _validate_reference_array(value, label: str) -> None:
    if not isinstance(value, list):
        raise SyncError(f"{label}必须是 UUID 数组")
    for item in value:
        _validate_reference(item, label)


def _validate_note_object(value) -> None:
    if not isinstance(value, dict):
        raise SyncError("备注必须是完整富文本对象")
    if set(value) != {"_t", "t", "ch", "v"}:
        raise SyncError("备注对象字段不完整")
    if value.get("_t") != "tx" or value.get("t") != 1:
        raise SyncError("只支持完整纯文本备注写入")
    text = value.get("v")
    checksum = value.get("ch")
    if not isinstance(text, str) or not isinstance(checksum, int):
        raise SyncError("备注内容或校验和类型错误")
    if checksum != (zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF):
        raise SyncError("备注 CRC32 校验和不匹配")


def validate_outbound_items(items: list[dict]) -> None:
    """Reject malformed or unverified Task7 payloads before any POST.

    Things Cloud treats UUIDs and item payloads as opaque in some paths.
    Invalid values therefore poison an account's history rather than merely
    failing the current request.
    """
    if not isinstance(items, list) or not items:
        raise SyncError("提交不能为空")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise SyncError("提交项必须是对象")
        item_uuid = item.get("uuid")
        _validate_reference(item_uuid, "条目")
        if item_uuid in seen:
            raise SyncError("同一提交中不能重复 UUID")
        seen.add(item_uuid)
        kind = item.get("e")
        action = item.get("t")
        payload = item.get("p")
        if kind not in ("Task6", "Task7", KIND_TAG):
            raise SyncError(f"不支持的实体类型: {kind}")
        if action not in (ACTION_CREATED, ACTION_MODIFIED):
            raise SyncError("只允许创建或修改操作")
        if not isinstance(payload, dict) or not payload:
            raise SyncError("条目 payload 必须是非空对象")

        if kind == KIND_TAG:
            name = payload.get("tt")
            if action != ACTION_CREATED or not isinstance(name, str) or not name.strip():
                raise SyncError("标签创建 payload 非法")
            continue

        if kind == "Task7" and action == ACTION_CREATED:
            if set(payload) != TASK7_CREATE_KEYS:
                missing = sorted(TASK7_CREATE_KEYS - set(payload))
                extra = sorted(set(payload) - TASK7_CREATE_KEYS)
                raise SyncError(
                    f"Task7 创建字段不完整 (缺失={missing}, 多余={extra})"
                )
            task_type = payload.get("tp")
            schedule = payload.get("st")
            if task_type not in (0, 1, 2) or schedule not in (ST_INBOX, ST_ANYTIME, ST_SOMEDAY):
                raise SyncError("Task7 创建类型或调度值非法")
            if task_type != 0 and schedule != ST_ANYTIME:
                raise SyncError("项目或标题只能放入随时")
            if payload.get("ss") != SS_PENDING or payload.get("tr") is not False:
                raise SyncError("新任务必须为待办且不在垃圾桶")
            if payload.get("rr") is not None or payload.get("rp") is not None:
                raise SyncError("安全写入不支持创建重复任务")
            if payload.get("rt") or payload.get("icsd") or payload.get("acrd"):
                raise SyncError("安全写入不支持重复实例")
            if payload.get("md") is not None:
                raise SyncError("Task7 创建时修改日期必须为 null")
            for key in ("ar", "pr", "agr", "tg"):
                _validate_reference_array(payload.get(key), key)
            if payload.get("ar") and payload.get("pr"):
                raise SyncError("任务不能同时属于区域与项目")
            _validate_note_object(payload.get("nt"))
            if schedule == ST_INBOX and (
                payload.get("sr") is not None or payload.get("tir") is not None
            ):
                raise SyncError("收件箱任务不能带调度日期")
            if payload.get("sr") != payload.get("tir"):
                raise SyncError("sr 与 tir 必须一致")
        elif kind == "Task7" and action == ACTION_MODIFIED:
            unknown = set(payload) - TASK7_MODIFY_KEYS
            if unknown:
                raise SyncError(f"Task7 修改包含未验证字段: {sorted(unknown)}")
            if not isinstance(payload.get("md"), (int, float)) or payload.get("md") <= 0:
                raise SyncError("Task7 修改必须带正数 md")
            if "nt" in payload:
                _validate_note_object(payload["nt"])
            for key in ("ar", "pr", "agr", "tg"):
                if key in payload:
                    _validate_reference_array(payload[key], key)
            if payload.get("ar") and payload.get("pr"):
                raise SyncError("任务不能同时属于区域与项目")
            has_status = "ss" in payload
            has_stop = "sp" in payload
            if has_status != has_stop:
                raise SyncError("状态与终止日期必须整体写入")
            if has_status:
                status = payload["ss"]
                if status not in (SS_PENDING, SS_COMPLETED):
                    raise SyncError("Task7 只支持待办或完成状态")
                if status == SS_PENDING and payload["sp"] is not None:
                    raise SyncError("恢复待办时 sp 必须为 null")
                if status == SS_COMPLETED and not payload["sp"]:
                    raise SyncError("完成任务时必须带 sp")
            schedule_keys = set(payload) & SAFE_TASK7_SCHEDULE_KEYS
            if schedule_keys and schedule_keys != SAFE_TASK7_SCHEDULE_KEYS:
                raise SyncError("调度必须整体写入 st/sr/tir")
            if schedule_keys:
                if payload["st"] not in (ST_INBOX, ST_ANYTIME, ST_SOMEDAY):
                    raise SyncError("调度位置 st 非法")
                if payload["st"] == ST_INBOX and payload["sr"] is not None:
                    raise SyncError("收件箱任务不能带调度日期")
                if payload["sr"] != payload["tir"]:
                    raise SyncError("sr 与 tir 必须一致")


def build_task7_create_payload(
    *,
    title: str,
    task_type: int = 0,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tag_ids: list[str] | None = None,
    project_uuid: str | None = None,
    area_uuid: str | None = None,
    sort_index: int = 0,
    now: int | None = None,
) -> dict:
    """Build the complete Task7 create object verified by the Go SDK."""
    title = _validate_title(title)
    if task_type not in (0, 1, 2):
        raise SyncError("任务类型必须是 0、1 或 2")
    if task_type != 0 and when not in (None, "", "anytime"):
        raise SyncError("项目和标题只能放入“随时”")
    if project_uuid and area_uuid:
        raise SyncError("任务不能同时属于项目与区域")

    st, sr = (ST_ANYTIME, None) if task_type != 0 else _resolve_when(when)
    if project_uuid or area_uuid:
        if when in (None, "", "inbox"):
            st, sr = ST_ANYTIME, None
    if st == ST_INBOX:
        sr = None

    timestamp = int(now if now is not None else _things_now())
    deadline_ts = _parse_deadline(deadline)
    note_object = notes_to_cloud(_validate_notes(notes))

    return {
        "tp": task_type,
        "sr": int(sr) if sr is not None else None,
        "dds": None,
        "rt": [],
        "rmd": None,
        "ss": SS_PENDING,
        "tr": False,
        "dl": [],
        "icp": False,
        "st": st,
        "ar": [area_uuid] if area_uuid else [],
        "tt": title,
        "do": 0,
        "lai": None,
        "tir": int(sr) if sr is not None else None,
        "tg": list(tag_ids or []),
        "agr": [],
        "ix": int(sort_index),
        "cd": timestamp,
        "lt": False,
        "icc": 0,
        "md": None,
        "ti": 0,
        "dd": deadline_ts,
        "ato": None,
        "nt": note_object,
        "icsd": None,
        "pr": [project_uuid] if project_uuid else [],
        "rp": None,
        "acrd": None,
        "sp": None,
        "sb": 0,
        "rr": None,
        "xx": {"sn": {}, "_t": "oo"},
    }


def _validate_title(title: str) -> str:
    if not isinstance(title, str) or not title.strip():
        raise SyncError("标题不能为空")
    title = title.strip()
    if len(title) > 10_000:
        raise SyncError("标题过长")
    return title


def _validate_notes(notes: str | None) -> str:
    if notes is None:
        return ""
    if not isinstance(notes, str):
        raise SyncError("备注必须是文本")
    if len(notes.encode("utf-8")) > 1_000_000:
        raise SyncError("备注超过 1MB 限制")
    return notes


def _parse_deadline(value) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SyncError("截止日期必须是 YYYY-MM-DD")
    try:
        return int(_date_to_ts(value.strip()))
    except ValueError as exc:
        raise SyncError(f"截止日期格式错误: {value}") from exc


def _validate_reference(value: str, label: str) -> str:
    if not isinstance(value, str) or not validate_uuid(value):
        raise SyncError(f"{label} UUID 非法")
    return value


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raw_task_state(items: list[dict]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for item in items:
        kind = str(item.get("e") or "")
        if not kind.startswith("Task"):
            continue
        item_uuid = item.get("uuid")
        payload = item.get("p")
        if not isinstance(item_uuid, str) or not isinstance(payload, dict):
            continue
        action = item.get("t", -1)
        if action == ACTION_CREATED or item_uuid not in state:
            state[item_uuid] = {"kind": kind, "p": dict(payload)}
        elif action == ACTION_MODIFIED and item_uuid in state:
            state[item_uuid]["p"].update(payload)
            state[item_uuid]["kind"] = kind
    return state


def _raw_meta_state(items: list[dict], kind: str) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for item in items:
        if item.get("e") != kind:
            continue
        item_uuid = item.get("uuid")
        payload = item.get("p")
        if not isinstance(item_uuid, str) or not isinstance(payload, dict):
            continue
        if item.get("t") == ACTION_CREATED or item_uuid not in state:
            state[item_uuid] = dict(payload)
        elif item.get("t") == ACTION_MODIFIED and item_uuid in state:
            state[item_uuid].update(payload)
    return state


class SafeThings:
    """High-level task/project API with durable offline synchronization."""

    def __init__(
        self,
        email: str,
        password: str,
        history_key: str | None = None,
        *,
        endpoint: str | None = None,
        data_dir: str | None = None,
        client=None,
    ):
        if client is not None:
            self.client = client
        else:
            client_kwargs = {}
            if endpoint:
                client_kwargs["endpoint"] = endpoint
            self.client = ThingsClient(email, password, **client_kwargs)
        if history_key:
            self.history_key = history_key
        else:
            info = self.client.verify()
            self.history_key = info.get("history-key") or info.get("uuid")
        if not self.history_key:
            raise SyncError("Things Cloud 未返回 history-key")

        self._lock = threading.RLock()
        self.data_dir = data_dir or self._default_data_dir(self.history_key)
        self._store = LocalStore(self.data_dir)

        self._raw_items = self._store.read("history_items.json", [])
        self._head_index = self._store.read("known_head.json", {}).get("head", 0)
        self._outbox = self._store.read("outbox.json", [])
        self._conflicts = self._store.read("conflicts.json", [])
        self._last_sync = self._store.read("sync_state.json", {}).get(
            "last_sync_at"
        )
        self._online = True
        self._last_error = ""
        self._tag_map: dict[str, str] = {}
        self._tasks = (
            _rebuild_state(self._raw_items, tag_map_out=self._tag_map)
            if self._raw_items
            else None
        )

        # Keep first launch responsive even when the machine is offline.
        self._refresh_best_effort()

    @staticmethod
    def _default_data_dir(history_key: str) -> str:
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        digest = hashlib.sha256(history_key.encode("utf-8")).hexdigest()[:24]
        return os.path.join(root, "ThingsWindows", "accounts", digest)

    # ---------- persistence and read cache ----------

    def _persist(self) -> None:
        self._store.write("history_items.json", self._raw_items)
        self._store.write("known_head.json", {"head": self._head_index})
        self._store.write("outbox.json", self._outbox)
        self._store.write("conflicts.json", self._conflicts)
        self._store.write(
            "sync_state.json",
            {
                "last_sync_at": self._last_sync,
                "online": self._online,
                "last_error": self._last_error,
            },
        )

    def _set_offline(self, exc: Exception) -> None:
        self._online = False
        self._last_error = str(exc)
        _log.warning("Things Cloud unavailable; using local cache: %s", exc)

    def _refresh_remote(self, *, full: bool = False) -> None:
        start = 0 if full else int(self._head_index or 0)
        new_items, new_head = self.client.sync_history_items(
            self.history_key, start
        )
        if full:
            self._raw_items = new_items
        else:
            self._raw_items.extend(new_items)
        self._head_index = int(new_head)
        self._last_sync = _iso_now()
        self._online = True
        self._last_error = ""
        self._tasks = _rebuild_state(
            self._raw_items, tag_map_out=self._tag_map
        )
        self._persist()

    def _refresh_best_effort(self) -> None:
        try:
            head = self.client.get_history_head(self.history_key)
            remote_head = int(head.get("latest-server-index", 0) or 0)
            cached_head = int(self._head_index or 0)
            if remote_head < cached_head:
                self._refresh_remote(full=True)
            elif remote_head > cached_head or self._tasks is None:
                self._refresh_remote(full=self._tasks is None)
            else:
                self._online = True
                self._last_error = ""
        except (requests.RequestException, ThingsCloudError, OSError, ValueError) as exc:
            self._set_offline(exc)

    def _pending_items(self) -> list[dict]:
        items: list[dict] = []
        for operation in self._outbox:
            for item in operation.get("items", []):
                items.append(copy.deepcopy(item))
        return items

    def _rebuild_projection(self) -> None:
        combined = self._raw_items + self._pending_items()
        self._tasks = _rebuild_state(combined, tag_map_out=self._tag_map)

    def get_all_tasks(self) -> list[dict]:
        with self._lock:
            self._refresh_best_effort()
            self._rebuild_projection()
            return copy.deepcopy(self._tasks or [])

    def get_tasks(
        self, list_name: str = "today", *, keyword: str | None = None
    ) -> list[dict]:
        tasks = self.get_all_tasks()
        return filter_tasks(
            tasks,
            only_pending=list_name != "trash",
            list_name=list_name,
            keyword=keyword,
        )

    def get_completed(self) -> list[dict]:
        return [
            task
            for task in self.get_all_tasks()
            if task.get("human_status") == "completed"
        ]

    def get_projects(self) -> list[dict]:
        return filter_tasks(
            self.get_all_tasks(),
            only_pending=False,
            task_type="project",
        )

    def get_tags(self) -> list[dict]:
        counts: dict[str, int] = {}
        for task in self.get_all_tasks():
            if task.get("trashed"):
                continue
            for tag in task.get("tags") or []:
                counts[tag] = counts.get(tag, 0) + 1
        return [
            {"tag": name, "count": count}
            for name, count in sorted(
                counts.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]

    def get_areas(self) -> list[dict]:
        state = _raw_meta_state(self._raw_items + self._pending_items(), KIND_AREA)
        result = []
        for area_uuid, payload in state.items():
            title = payload.get("tt") or payload.get("ti") or ""
            if isinstance(title, str) and title.strip():
                result.append({"uuid": area_uuid, "title": title.strip()})
        result.sort(key=lambda item: (item["title"].lower(), item["uuid"]))
        return result

    # ---------- item construction ----------

    def _task_state(self) -> dict[str, dict]:
        return _raw_task_state(self._raw_items + self._pending_items())

    def _current_payload(self, task_uuid: str) -> dict:
        _validate_reference(task_uuid, "任务")
        state = self._task_state().get(task_uuid)
        if not state:
            raise SyncError("找不到要修改的任务或项目")
        return copy.deepcopy(state["p"])

    def _base_payload(self, task_uuid: str, current: dict) -> dict:
        """Return the last server-confirmed payload for conflict snapshots.

        Projected state includes pending local edits. Those edits must not
        become the concurrency baseline, otherwise a second offline edit to
        the same field looks like an external modification on reconnect.
        """
        remote = _raw_task_state(self._raw_items).get(task_uuid)
        if remote and isinstance(remote.get("p"), dict):
            return copy.deepcopy(remote["p"])
        return current

    def _modify_entity_kind(self, current: dict, action: str) -> str:
        payload = current.get("p", {})
        repeating = (
            payload.get("rr") is not None
            or payload.get("rp") is not None
            or bool(payload.get("rt"))
            or payload.get("icsd") is not None
            or payload.get("acrd") is not None
        )
        if repeating:
            raise SyncError("这是重复任务或重复实例，安全写入已拒绝修改")
        if action == "cancel":
            return "Task6"
        if current.get("kind") == "Task7" and all(
            key in payload for key in ("rr", "rp", "rt")
        ):
            return "Task7"
        # Legacy records and sparse Task6 histories keep the envelope that the
        # existing account already uses. This preserves native compatibility.
        return "Task6"

    def _tag_ids_for_names(self, names: list[str] | None) -> tuple[list[str], list[dict]]:
        tag_state = _raw_meta_state(
            self._raw_items + self._pending_items(), KIND_TAG
        )
        name_to_uuid = {
            str(payload.get("tt") or payload.get("ti") or "").strip(): tag_uuid
            for tag_uuid, payload in tag_state.items()
        }
        name_to_uuid.pop("", None)
        result: list[str] = []
        items: list[dict] = []
        seen: set[str] = set()
        now = _things_now()
        for raw_name in names or []:
            if not isinstance(raw_name, str):
                raise SyncError("标签必须是文本")
            name = raw_name.strip()
            if not name:
                continue
            if len(name) > 200:
                raise SyncError(f"标签过长: {name[:20]}…")
            tag_uuid = name_to_uuid.get(name)
            if not tag_uuid:
                tag_uuid = new_uuid()
                name_to_uuid[name] = tag_uuid
                items.append(
                    {
                        "uuid": tag_uuid,
                        "e": KIND_TAG,
                        "t": ACTION_CREATED,
                        "p": {
                            "tt": name,
                            "ix": 0,
                            "pn": [],
                            "sh": None,
                            "xx": {"sn": {}, "_t": "oo"},
                            "cd": now,
                            "md": now,
                        },
                    }
                )
            if tag_uuid not in seen:
                result.append(tag_uuid)
                seen.add(tag_uuid)
        return result, items

    def _validate_project(self, project_uuid: str | None) -> None:
        if not project_uuid:
            return
        _validate_reference(project_uuid, "项目")
        task = self._task_state().get(project_uuid)
        if not task:
            raise SyncError("目标项目不存在")
        try:
            is_project = int(task["p"].get("tp", 0) or 0) == 1
        except (TypeError, ValueError):
            is_project = False
        if not is_project:
            raise SyncError("目标任务不是项目")

    def _validate_area(self, area_uuid: str | None) -> None:
        if not area_uuid:
            return
        _validate_reference(area_uuid, "区域")
        if area_uuid not in _raw_meta_state(self._raw_items, KIND_AREA):
            raise SyncError("目标区域不存在")

    def _sort_index(self, st: int, project_uuid: str | None = None) -> int:
        values: list[float] = []
        for task in self.get_all_tasks():
            if task.get("trashed") or task.get("st_code") != st:
                continue
            if project_uuid and project_uuid not in (
                task.get("project_uuids") or []
            ):
                continue
            value = task.get("ix")
            if isinstance(value, (int, float)):
                values.append(float(value))
        return int(min(values) - 100) if values else 0

    # ---------- write queue ----------

    def _queue_operation(
        self,
        *,
        action: str,
        task_uuid: str,
        entity_kind: str,
        items: list[dict],
        payload: dict,
        base_fields: dict | None,
    ) -> str:
        if not items:
            return ""
        validate_outbound_items(items)
        operation = {
            "id": uuid_mod.uuid4().hex,
            "action": action,
            "uuid": task_uuid,
            "entity_kind": entity_kind,
            "items": items,
            "base_fields": copy.deepcopy(base_fields),
            "created_at": _iso_now(),
            "attempts": 0,
            "last_error": "",
        }
        if self._coalesce_operation(operation):
            self._persist()
            self._rebuild_projection()
            self._flush_locked()
            return operation["id"]
        self._outbox.append(operation)
        self._persist()
        self._rebuild_projection()
        self._flush_locked()
        return operation["id"]

    def _coalesce_operation(self, incoming: dict) -> bool:
        """Merge consecutive local edits before they reach the server.

        The offline outbox may contain several edits from this same device.
        Sending them one by one makes a stale local baseline look like a
        remote conflict. Merge only same-envelope edits for the same entity;
        the earliest base fields remain the concurrency baseline.
        """
        if incoming.get("action") == "create":
            return False
        for existing in reversed(self._outbox):
            if existing.get("uuid") != incoming.get("uuid"):
                continue
            # A create is a canonical 34-field Task7 snapshot and must remain
            # pending until the server accepts it. Folding a later completion
            # or edit into that snapshot produces an invalid create payload.
            if existing.get("action") == "create":
                return False
            existing_items = existing.get("items") or []
            incoming_items = incoming.get("items") or []
            existing_task = next(
                (
                    item
                    for item in existing_items
                    if item.get("uuid") == incoming.get("uuid")
                ),
                None,
            )
            incoming_task = next(
                (
                    item
                    for item in incoming_items
                    if item.get("uuid") == incoming.get("uuid")
                ),
                None,
            )
            if not existing_task or not incoming_task:
                return False
            if existing_task.get("e") != incoming_task.get("e"):
                return False

            merged_payload = copy.deepcopy(existing_task.get("p") or {})
            for key, value in (incoming_task.get("p") or {}).items():
                if existing.get("action") == "create" and key == "md":
                    continue
                merged_payload[key] = copy.deepcopy(value)
            existing_task["p"] = merged_payload

            if existing.get("action") != "create":
                base_fields = copy.deepcopy(existing.get("base_fields") or {})
                for key, value in (incoming.get("base_fields") or {}).items():
                    base_fields.setdefault(key, copy.deepcopy(value))
                existing["base_fields"] = base_fields
                existing["action"] = incoming.get("action")
                existing["entity_kind"] = incoming.get("entity_kind")

            known_uuids = {item.get("uuid") for item in existing_items}
            for item in incoming_items:
                if item.get("uuid") == incoming.get("uuid"):
                    continue
                if item.get("uuid") in known_uuids:
                    continue
                existing_items.append(copy.deepcopy(item))
                known_uuids.add(item.get("uuid"))
            existing["last_error"] = ""
            validate_outbound_items(existing_items)
            return True
        return False

    def _remove_outbox_operation(self, operation: dict) -> bool:
        operation_id = operation.get("id")
        before = len(self._outbox)
        self._outbox[:] = [
            item
            for item in self._outbox
            if not operation_id or item.get("id") != operation_id
        ]
        return len(self._outbox) != before

    def _remote_field_conflict(
        self, operation: dict, remote_items: list[dict]
    ) -> bool:
        task_uuid = operation["uuid"]
        remote_state = _raw_task_state(remote_items)
        current = remote_state.get(task_uuid)
        if operation["action"] == "create":
            return current is not None
        if current is None:
            return True
        base_fields = operation.get("base_fields")
        if not isinstance(base_fields, dict):
            return False
        remote_payload = current.get("p", {})
        for key, base_value in base_fields.items():
            remote_value = remote_payload.get(key)
            if key in ("tg", "pr", "ar", "agr") and isinstance(
                remote_value, list
            ) and isinstance(base_value, list):
                same = set(remote_value) == set(base_value)
            else:
                same = remote_value == base_value
            if not same:
                return True
        return False

    def _add_conflict(self, operation: dict, reason: str) -> None:
        conflict = copy.deepcopy(operation)
        conflict["reason"] = reason
        conflict["detected_at"] = _iso_now()
        self._conflicts.append(conflict)

    def _flush_locked(self) -> None:
        if not self._outbox:
            return
        for operation in list(self._outbox):
            attempts = int(operation.get("attempts", 0))
            while attempts < 3:
                attempts += 1
                operation["attempts"] = attempts
                try:
                    head = self.client.get_history_head(self.history_key)
                    remote_head = int(
                        head.get("latest-server-index", 0) or 0
                    )
                    if remote_head < int(self._head_index or 0):
                        remote_items, remote_head = self.client.sync_history_items(
                            self.history_key, 0
                        )
                        self._raw_items = remote_items
                    elif remote_head > int(self._head_index or 0):
                        remote_items, remote_head = self.client.sync_history_items(
                            self.history_key, int(self._head_index or 0)
                        )
                        self._raw_items.extend(remote_items)
                    self._head_index = int(remote_head)
                    self._online = True
                    self._last_error = ""

                    # Pull before retrying an offline write. Selecting a fresh
                    # ancestor without examining new history would silently
                    # overwrite a same-field edit made on another device.
                    if self._remote_field_conflict(
                        operation, self._raw_items
                    ):
                        self._add_conflict(
                            operation,
                            "同一字段已在另一台设备上修改",
                        )
                        self._remove_outbox_operation(operation)
                        self._persist()
                        self._rebuild_projection()
                        break
                    ancestor = remote_head
                    new_head = self.client.commit_once(
                        self.history_key,
                        operation["items"],
                        ancestor,
                    )
                except ThingsConflictError as exc:
                    try:
                        remote_items, remote_head = self.client.sync_history_items(
                            self.history_key, 0
                        )
                    except (requests.RequestException, ThingsCloudError) as net_exc:
                        operation["last_error"] = str(net_exc)
                        self._set_offline(net_exc)
                        self._persist()
                        return
                    self._raw_items = remote_items
                    self._head_index = remote_head
                    if self._remote_field_conflict(operation, remote_items):
                        self._add_conflict(
                            operation,
                            "同一字段已在另一台设备上修改",
                        )
                        self._remove_outbox_operation(operation)
                        self._persist()
                        break
                    operation["last_error"] = str(exc)
                    continue
                except (requests.RequestException, ThingsCloudError, OSError) as exc:
                    operation["last_error"] = str(exc)
                    self._set_offline(exc)
                    self._persist()
                    return

                self._raw_items.extend(copy.deepcopy(operation["items"]))
                self._head_index = int(new_head)
                self._last_sync = _iso_now()
                self._online = True
                self._last_error = ""
                self._remove_outbox_operation(operation)
                self._persist()
                self._rebuild_projection()
                break
            else:
                self._add_conflict(
                    operation,
                    "提交多次冲突，已暂停以避免覆盖其他设备",
                )
                self._remove_outbox_operation(operation)
                self._persist()

    def flush(self) -> dict:
        with self._lock:
            self._flush_locked()
            self._rebuild_projection()
            return self.sync_status()

    def sync_status(self) -> dict:
        return {
            "online": self._online,
            "pending": len(self._outbox),
            "conflicts": len(self._conflicts),
            "last_sync_at": self._last_sync,
            "last_error": self._last_error,
            "cache_dir": self.data_dir,
        }

    def get_conflicts(self) -> list[dict]:
        result = []
        for conflict in self._conflicts:
            result.append(
                {
                    "id": conflict.get("id"),
                    "action": conflict.get("action"),
                    "uuid": conflict.get("uuid"),
                    "reason": conflict.get("reason"),
                    "detected_at": conflict.get("detected_at"),
                    "fields": sorted((conflict.get("base_fields") or {}).keys()),
                }
            )
        return result

    def resolve_conflict(self, conflict_id: str, *, retry: bool = False) -> dict:
        with self._lock:
            conflict = next(
                (c for c in self._conflicts if c.get("id") == conflict_id),
                None,
            )
            if conflict is None:
                raise SyncError("冲突记录不存在")
            self._conflicts.remove(conflict)
            if retry:
                self._refresh_remote(full=True)
                current = _raw_task_state(self._raw_items).get(conflict["uuid"])
                if conflict["action"] == "create":
                    if current is not None:
                        self._persist()
                        raise SyncError("项目或任务已在云端创建，不能重复重试")
                    conflict["base_fields"] = None
                else:
                    if current is None:
                        self._persist()
                        raise SyncError("任务或项目已不存在")
                    conflict["base_fields"] = {
                        key: current["p"].get(key)
                        for key in (conflict.get("base_fields") or {})
                    }
                conflict["attempts"] = 0
                conflict["last_error"] = ""
                self._outbox.append(conflict)
                self._persist()
                self._flush_locked()
            else:
                self._persist()
            return self.sync_status()

    # ---------- task and project operations ----------

    def add_task(
        self,
        title: str,
        notes: str | None = None,
        when: str | None = None,
        deadline: str | None = None,
        tags: list[str] | None = None,
        project_uuid: str | None = None,
        area_uuid: str | None = None,
    ) -> str:
        with self._lock:
            self._validate_project(project_uuid)
            self._validate_area(area_uuid)
            tag_ids, tag_items = self._tag_ids_for_names(tags)
            st, _ = _resolve_when(when)
            if project_uuid or area_uuid:
                if when in (None, "", "inbox"):
                    st = ST_ANYTIME
            task_uuid = new_uuid()
            payload = build_task7_create_payload(
                title=title,
                task_type=0,
                notes=notes,
                when=when,
                deadline=deadline,
                tag_ids=tag_ids,
                project_uuid=project_uuid,
                area_uuid=area_uuid,
                sort_index=self._sort_index(st, project_uuid),
            )
            items = tag_items + [
                {
                    "uuid": task_uuid,
                    "e": "Task7",
                    "t": ACTION_CREATED,
                    "p": payload,
                }
            ]
            self._queue_operation(
                action="create",
                task_uuid=task_uuid,
                entity_kind="Task7",
                items=items,
                payload=payload,
                base_fields=None,
            )
            return task_uuid

    def add_project(
        self,
        title: str,
        notes: str | None = None,
        deadline: str | None = None,
        tags: list[str] | None = None,
        area_uuid: str | None = None,
    ) -> str:
        with self._lock:
            self._validate_area(area_uuid)
            tag_ids, tag_items = self._tag_ids_for_names(tags)
            project_uuid = new_uuid()
            payload = build_task7_create_payload(
                title=title,
                task_type=1,
                notes=notes,
                when="anytime",
                deadline=deadline,
                tag_ids=tag_ids,
                area_uuid=area_uuid,
                sort_index=self._sort_index(ST_ANYTIME),
            )
            items = tag_items + [
                {
                    "uuid": project_uuid,
                    "e": "Task7",
                    "t": ACTION_CREATED,
                    "p": payload,
                }
            ]
            self._queue_operation(
                action="create",
                task_uuid=project_uuid,
                entity_kind="Task7",
                items=items,
                payload=payload,
                base_fields=None,
            )
            return project_uuid

    def modify_task_fields(self, task_uuid: str, fields: dict) -> dict:
        with self._lock:
            if not isinstance(fields, dict) or not fields:
                return {"ok": False, "changed": []}
            current = self._current_payload(task_uuid)
            current_state = self._task_state().get(task_uuid) or {}
            entity_kind = self._modify_entity_kind(current_state, "modify")
            base_payload = self._base_payload(task_uuid, current)
            payload: dict = {"md": _things_now()}
            base_fields: dict = {}
            changed: list[str] = []

            if "tt" in fields:
                title = _validate_title(fields.get("tt"))
                payload["tt"] = title
                base_fields["tt"] = base_payload.get("tt") or base_payload.get("ti")
                changed.append("title")

            if "nt" in fields:
                note_text = _validate_notes(fields.get("nt"))
                payload["nt"] = notes_to_cloud(note_text)
                base_fields["nt"] = base_payload.get("nt")
                changed.append("notes")

            if "tg" in fields:
                names = fields.get("tg")
                if not isinstance(names, list):
                    raise SyncError("标签必须是数组")
                tag_ids, tag_items = self._tag_ids_for_names(names)
                payload["tg"] = tag_ids
                base_fields["tg"] = base_payload.get("tg") or []
                changed.append("tags")
            else:
                tag_items = []

            if "dd" in fields:
                payload["dd"] = _parse_deadline(fields.get("dd"))
                base_fields["dd"] = base_payload.get("dd")
                changed.append("deadline")

            if "when" in fields:
                st, sr = _resolve_when(fields.get("when"))
                payload.update(
                    {
                        "st": st,
                        "sr": int(sr) if sr is not None else None,
                        "tir": int(sr) if sr is not None else None,
                    }
                )
                for key in ("st", "sr", "tir"):
                    base_fields[key] = base_payload.get(key)
                changed.append("schedule")
            elif "st" in fields or "sr" in fields or "tir" in fields:
                raise SyncError("调度必须通过 when 字段整体修改")

            if "project_uuid" in fields or "area_uuid" in fields:
                project_uuid = fields.get("project_uuid") or None
                area_uuid = fields.get("area_uuid") or None
                if isinstance(project_uuid, list):
                    project_uuid = project_uuid[0] if project_uuid else None
                if isinstance(area_uuid, list):
                    area_uuid = area_uuid[0] if area_uuid else None
                if project_uuid and area_uuid:
                    raise SyncError("任务不能同时属于项目与区域")
                if project_uuid == task_uuid:
                    raise SyncError("任务不能成为自己的子项")
                current_type = int(current.get("tp", 0) or 0)
                if project_uuid and current_type in (1, 2):
                    raise SyncError("项目或标题不能放入另一个项目")
                self._validate_project(project_uuid)
                self._validate_area(area_uuid)
                if project_uuid:
                    payload.update(
                        {"pr": [project_uuid], "ar": [], "agr": []}
                    )
                elif area_uuid:
                    payload.update(
                        {"pr": [], "ar": [area_uuid], "agr": []}
                    )
                else:
                    payload.update({"pr": [], "ar": [], "agr": []})
                if "when" not in fields:
                    st, sr = ST_ANYTIME, None
                    payload.update(
                        {
                            "st": st,
                            "sr": None,
                            "tir": None,
                        }
                    )
                for key in ("pr", "ar", "agr", "st", "sr", "tir"):
                    base_fields[key] = base_payload.get(key)
                changed.append("location")

            if "cd" in fields:
                raise SyncError("当前安全契约不支持修改创建日期")

            if len(payload) == 1:
                return {"ok": True, "changed": []}
            extra = copy.deepcopy(tag_items)
            extra.append(
                {
                    "uuid": task_uuid,
                    "e": entity_kind,
                    "t": ACTION_MODIFIED,
                    "p": payload,
                }
            )
            self._queue_operation(
                action="modify",
                task_uuid=task_uuid,
                entity_kind=entity_kind,
                items=extra,
                payload=payload,
                base_fields=base_fields,
            )
            return {"ok": True, "changed": changed}

    def set_status(self, task_uuid: str, status: str) -> bool:
        with self._lock:
            current = self._current_payload(task_uuid)
            current_state = self._task_state().get(task_uuid) or {}
            base_payload = self._base_payload(task_uuid, current)
            now = _things_now()
            status = str(status or "").lower().strip()
            if status == "pending":
                payload = {"ss": SS_PENDING, "sp": None, "md": now}
                kind = self._modify_entity_kind(current_state, "status")
                base_fields = {
                    "ss": base_payload.get("ss", 0),
                    "sp": base_payload.get("sp"),
                }
            elif status == "completed":
                payload = {"ss": SS_COMPLETED, "sp": now, "md": now}
                kind = self._modify_entity_kind(current_state, "status")
                base_fields = {
                    "ss": base_payload.get("ss", 0),
                    "sp": base_payload.get("sp"),
                }
            elif status == "canceled":
                # The verified Task7 patch scope only covers pending/completed.
                # Task6 cancellation is retained for compatibility.
                payload = {"ss": SS_CANCELED, "sp": now, "md": now}
                kind = self._modify_entity_kind(current_state, "cancel")
                base_fields = {
                    "ss": base_payload.get("ss", 0),
                    "sp": base_payload.get("sp"),
                }
            else:
                raise SyncError("状态必须是 pending、completed 或 canceled")
            self._queue_operation(
                action="status",
                task_uuid=task_uuid,
                entity_kind=kind,
                items=[
                    {
                        "uuid": task_uuid,
                        "e": kind,
                        "t": ACTION_MODIFIED,
                        "p": payload,
                    }
                ],
                payload=payload,
                base_fields=base_fields,
            )
            return True

    def trash_task(self, task_uuid: str, *, trashed: bool = True) -> bool:
        with self._lock:
            current = self._current_payload(task_uuid)
            current_state = self._task_state().get(task_uuid) or {}
            base_payload = self._base_payload(task_uuid, current)
            kind = self._modify_entity_kind(current_state, "trash")
            payload = {"tr": bool(trashed), "md": _things_now()}
            self._queue_operation(
                action="trash" if trashed else "restore",
                task_uuid=task_uuid,
                entity_kind=kind,
                items=[
                    {
                        "uuid": task_uuid,
                        "e": kind,
                        "t": ACTION_MODIFIED,
                        "p": payload,
                    }
                ],
                payload=payload,
                base_fields={"tr": base_payload.get("tr", False)},
            )
            return True

    def trash_tasks_batch(self, task_uuids: list[str]) -> int:
        if not isinstance(task_uuids, list):
            raise SyncError("uuids 必须是数组")
        unique_uuids = []
        seen = set()
        for task_uuid in task_uuids:
            task_uuid = str(task_uuid or "")
            if task_uuid in seen:
                continue
            # Validate the whole batch first so one malformed UUID cannot
            # leave the user with a partially applied trash operation.
            self._current_payload(task_uuid)
            seen.add(task_uuid)
            unique_uuids.append(task_uuid)
        for task_uuid in unique_uuids:
            self.trash_task(task_uuid)
        return len(unique_uuids)

    def move_schedule(self, task_uuid: str, target: str) -> bool:
        with self._lock:
            current = self._current_payload(task_uuid)
            current_state = self._task_state().get(task_uuid) or {}
            base_payload = self._base_payload(task_uuid, current)
            kind = self._modify_entity_kind(current_state, "schedule")
            target_text = str(target or "").lower().strip()
            if target_text in ("inbox", ""):
                st, sr = ST_INBOX, None
            elif target_text == "today":
                st, sr = _resolve_when("today")
            elif target_text == "tomorrow":
                st, sr = _resolve_when("tomorrow")
            elif target_text in ("anytime", "scheduled"):
                st, sr = ST_ANYTIME, None
            elif target_text in ("someday", "upcoming"):
                st, sr = ST_SOMEDAY, None
            else:
                try:
                    _date_to_ts(target_text)
                except ValueError as exc:
                    raise SyncError(f"未知目标: {target}") from exc
                st, sr = ST_SOMEDAY, _date_to_ts(target_text)
            sr_value = int(sr) if sr is not None else None
            payload = {
                "st": st,
                "sr": sr_value,
                "tir": sr_value,
                "ix": self._sort_index(st),
                "md": _things_now(),
            }
            self._queue_operation(
                action="schedule",
                task_uuid=task_uuid,
                entity_kind=kind,
                items=[
                    {
                        "uuid": task_uuid,
                        "e": kind,
                        "t": ACTION_MODIFIED,
                        "p": payload,
                    }
                ],
                payload=payload,
                base_fields={
                    "st": base_payload.get("st"),
                    "sr": base_payload.get("sr"),
                    "tir": base_payload.get("tir"),
                    "ix": base_payload.get("ix"),
                },
            )
            return True

    def reorder_tasks(self, task_uuids: list[str]) -> int:
        with self._lock:
            if not task_uuids or len(task_uuids) < 2:
                return 0
            all_tasks = {task["uuid"]: task for task in self.get_all_tasks()}
            targets = [all_tasks[u] for u in task_uuids if u in all_tasks]
            if len(targets) < 2:
                return 0
            indexes = sorted(
                float(task["ix"])
                for task in targets
                if isinstance(task.get("ix"), (int, float))
            )
            if len(indexes) != len(targets):
                base = min(indexes) if indexes else 0
                indexes = [
                    base - 100 * (len(targets) - 1 - position)
                    for position in range(len(targets))
                ]
            changed = 0
            now = _things_now()
            for task, index in zip(targets, indexes):
                new_index = int(index) if float(index).is_integer() else index
                if task.get("ix") == new_index:
                    continue
                current = self._current_payload(task["uuid"])
                current_state = self._task_state().get(task["uuid"]) or {}
                base_payload = self._base_payload(task["uuid"], current)
                kind = self._modify_entity_kind(current_state, "reorder")
                payload = {"ix": new_index, "md": now}
                self._queue_operation(
                    action="reorder",
                    task_uuid=task["uuid"],
                    entity_kind=kind,
                    items=[
                        {
                            "uuid": task["uuid"],
                            "e": kind,
                            "t": ACTION_MODIFIED,
                            "p": payload,
                        }
                    ],
                    payload=payload,
                    base_fields={"ix": base_payload.get("ix")},
                )
                changed += 1
            return changed
