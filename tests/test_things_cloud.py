from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta

import pytest

from things_cloud import (
    ACTION_CREATED,
    ACTION_DELETED,
    ACTION_MODIFIED,
    KIND_TAG,
    KIND_TOMBSTONE,
    KIND_TASK7,
    SS_CANCELED,
    SS_COMPLETED,
    SS_PENDING,
    ST_ANYTIME,
    ST_INBOX,
    ST_SOMEDAY,
    THINGS_APP_ID,
    THINGS_USER_AGENT,
    ThingsConflictError,
    ThingsClient,
    _rebuild_state,
    _server_confirmed_items,
    _date_to_ts,
    canonical_legacy_identifier,
    filter_tasks,
    new_uuid,
)


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, get_responses=None, post_responses=None):
        self.timeout = None
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self.post_responses:
            return self.post_responses.pop(0)
        return FakeResponse(200, {"server-head-index": 7})


def test_history_sync_assigns_one_server_index_per_bucket_and_pages():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "items": [
                        {
                            new_uuid(): {
                                "e": "Task7",
                                "t": 1,
                                "p": {"tt": "A"},
                            },
                            new_uuid(): {
                                "e": "Tag4",
                                "t": 1,
                                "p": {"tt": "B"},
                            },
                        },
                    ],
                    "current-item-index": 2,
                },
            ),
            FakeResponse(
                200,
                {
                    "items": [
                        {new_uuid(): {"e": "Task7", "t": 1, "p": {"tt": "C"}}}
                    ],
                    "current-item-index": 2,
                },
            ),
        ]
    )
    client = ThingsClient("test@example.com", "secret")
    client.session = session

    items, head = client.sync_history_items("history", 0)

    assert head == 2
    assert [item["server_index"] for item in items] == [0, 0, 1]
    assert session.get_calls[0][1]["params"] == {"start-index": 0}
    assert session.get_calls[1][1]["params"] == {"start-index": 1}


def test_commit_uses_verified_mac_identity_and_commit_count():
    session = FakeSession()
    client = ThingsClient("test@example.com", "secret")
    client.session = session
    items = [
        {
            "uuid": new_uuid(),
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {"tt": "Task"},
        },
        {
            "uuid": new_uuid(),
            "e": "Tag4",
            "t": ACTION_CREATED,
            "p": {"tt": "Tag"},
        },
    ]

    head = client.commit_once("history", items, 6)

    assert head == 7
    _, request = session.post_calls[0]
    headers = request["headers"]
    assert request["params"] == {"ancestor-index": 6, "_cnt": 1}
    assert headers["Push-Priority"] == "5"
    assert headers["App-Id"] == THINGS_APP_ID
    assert headers["App-Id"] == "com.culturedcode.ThingsMac"
    assert headers["User-Agent"] == THINGS_USER_AGENT
    assert headers["User-Agent"].startswith("ThingsMac/")
    client_info = json.loads(
        base64.b64decode(headers["Things-Client-Info"]).decode("utf-8")
    )
    assert client_info["nn"] == "ThingsMac"
    assert client_info["on"] == "macOS"


def test_commit_conflict_is_not_retried_automatically():
    session = FakeSession(
        post_responses=[
            FakeResponse(409, {"latest-server-index": 9}, "conflict")
        ]
    )
    client = ThingsClient("test@example.com", "secret")
    client.session = session
    items = [
        {
            "uuid": new_uuid(),
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {"tt": "Task"},
        }
    ]

    with pytest.raises(ThingsConflictError) as exc_info:
        client.commit_once("history", items, 6)

    assert exc_info.value.latest_server_index == 9
    assert len(session.post_calls) == 1


def test_today_uses_exact_scheduled_date_and_other_lists_ignore_stale_sr():
    today = date.today()
    old = today - timedelta(days=1200)
    future = today + timedelta(days=14)

    def task(title, scheduled, *, st=ST_ANYTIME, schedule="anytime"):
        return {
            "uuid": new_uuid(),
            "title": title,
            "notes": "",
            "human_status": "pending",
            "trashed": False,
            "type": "task",
            "schedule": schedule,
            "st_code": st,
            "scheduled_ms": _date_to_ts(scheduled) if scheduled else None,
            "scheduled_date": scheduled,
            "deadline_ms": None,
            "deadline_date": "",
            "created_ms": 1_700_000_000,
            "ix": 0,
            "tags": [],
        }

    tasks = [
        task("Today", today.isoformat()),
        task("Someday today", today.isoformat(), st=ST_SOMEDAY, schedule="someday"),
        task("Old dated", old.isoformat()),
        task("Future", future.isoformat(), st=ST_SOMEDAY, schedule="someday"),
        task("Anytime future", future.isoformat()),
        task("Anytime", None),
        task("Someday", None, st=ST_SOMEDAY, schedule="someday"),
        task("Inbox", None, st=ST_INBOX, schedule="inbox"),
    ]

    assert [t["title"] for t in filter_tasks(tasks, list_name="today")] == [
        "Today"
    ]
    assert [t["title"] for t in filter_tasks(tasks, list_name="anytime")] == [
        "Anytime"
    ]
    assert [t["title"] for t in filter_tasks(tasks, list_name="someday")] == [
        "Someday"
    ]
    upcoming = filter_tasks(tasks, list_name="upcoming")
    assert {t["title"] for t in upcoming} == {"Future", "Anytime future"}
    assert [t["scheduled_date"] for t in upcoming] == sorted(
        t["scheduled_date"] for t in upcoming
    )


def test_upcoming_includes_every_future_scheduled_task_in_date_order():
    today = date.today()

    def task(title, days_ahead, *, st):
        scheduled = (today + timedelta(days=days_ahead)).isoformat()
        return {
            "uuid": new_uuid(),
            "title": title,
            "notes": "",
            "human_status": "pending",
            "trashed": False,
            "type": "task",
            "schedule": "anytime" if st == ST_ANYTIME else "someday",
            "st_code": st,
            "scheduled_ms": _date_to_ts(scheduled),
            "scheduled_date": scheduled,
            "deadline_ms": None,
            "deadline_date": "",
            "created_ms": 1_700_000_000,
            "ix": 0,
            "tags": [],
        }

    tasks = [
        task("Later someday", 10, st=ST_SOMEDAY),
        task("Sooner anytime", 2, st=ST_ANYTIME),
        task("Past", -2, st=ST_ANYTIME),
    ]

    assert [
        item["title"]
        for item in filter_tasks(tasks, list_name="upcoming")
    ] == ["Sooner anytime", "Later someday"]


def test_uncursored_complete_creation_is_kept_but_sparse_change_is_hidden():
    complete_uuid = new_uuid()
    sparse_uuid = new_uuid()
    items = [
        {
            "uuid": complete_uuid,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {
                "tt": "串口工具",
                "st": ST_ANYTIME,
                "ss": SS_PENDING,
                "tp": 0,
                "sr": _date_to_ts(date.today().isoformat()),
            },
        },
        {
            "uuid": sparse_uuid,
            "e": KIND_TASK7,
            "t": ACTION_MODIFIED,
            "p": {"tt": "Dirty legacy projection", "st": ST_INBOX, "ss": 0},
        },
    ]

    confirmed = _server_confirmed_items(items)

    assert [item["uuid"] for item in confirmed] == [complete_uuid]


def test_today_falls_back_to_timestamp_when_formatted_date_is_missing():
    today = datetime.now().date()
    old = today - timedelta(days=800)

    def task(title, scheduled):
        return {
            "uuid": new_uuid(),
            "title": title,
            "notes": "",
            "human_status": "pending",
            "trashed": False,
            "type": "task",
            "schedule": "anytime",
            "st_code": ST_ANYTIME,
            "scheduled_ms": _date_to_ts(scheduled.isoformat()),
            "scheduled_date": "",
            "deadline_ms": None,
            "deadline_date": "",
            "created_ms": 1_700_000_000,
            "ix": 0,
            "tags": [],
        }

    tasks = [task("Today", today), task("Old", old)]

    assert [t["title"] for t in filter_tasks(tasks, list_name="today")] == [
        "Today"
    ]


def test_rebuild_replays_delete_tombstone_and_recreate_in_history_order():
    task_uuid = new_uuid()
    items = [
        {
            "uuid": task_uuid,
            "e": KIND_TASK7,
            "t": ACTION_MODIFIED,
            "p": {"tt": "Ghost"},
            "server_index": 0,
        },
        {
            "uuid": task_uuid,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {"tt": "First", "st": ST_INBOX, "ss": SS_PENDING, "tp": 0},
            "server_index": 1,
        },
        {
            "uuid": task_uuid,
            "e": KIND_TASK7,
            "t": ACTION_DELETED,
            "p": {},
            "server_index": 2,
        },
        {
            "uuid": new_uuid(),
            "e": KIND_TOMBSTONE,
            "t": ACTION_CREATED,
            "p": {"dloid": task_uuid},
            "server_index": 3,
        },
        {
            "uuid": task_uuid,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {"tt": "Restored", "st": ST_INBOX, "ss": SS_PENDING, "tp": 0},
            "server_index": 4,
        },
    ]

    tasks = _rebuild_state(items)

    assert [task["title"] for task in tasks] == ["Restored"]


def test_rebuild_clears_deleted_tags_from_tag_map_and_tasks():
    tag_uuid = new_uuid()
    task_uuid = new_uuid()
    tag_map = {}
    items = [
        {
            "uuid": tag_uuid,
            "e": KIND_TAG,
            "t": ACTION_CREATED,
            "p": {"tt": "old-tag"},
            "server_index": 0,
        },
        {
            "uuid": task_uuid,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {
                "tt": "Tagged",
                "st": ST_INBOX,
                "ss": SS_PENDING,
                "tp": 0,
                "tg": [tag_uuid],
            },
            "server_index": 1,
        },
        {
            "uuid": tag_uuid,
            "e": KIND_TAG,
            "t": ACTION_DELETED,
            "p": {},
            "server_index": 2,
        },
    ]

    tasks = _rebuild_state(items, tag_map_out=tag_map)

    assert tag_map == {}
    assert tasks[0]["tags"] == []


def test_smart_lists_hide_recurring_templates_and_inactive_ancestors():
    today = date.today().isoformat()
    completed_project = new_uuid()
    recurring_uuid = new_uuid()
    hidden_child = new_uuid()
    items = [
        {
            "uuid": completed_project,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {
                "tt": "Finished project",
                "tp": 1,
                "st": ST_INBOX,
                "ss": SS_COMPLETED,
            },
            "server_index": 0,
        },
        {
            "uuid": recurring_uuid,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {
                "tt": "Weekly template",
                "tp": 0,
                "st": ST_INBOX,
                "ss": SS_PENDING,
                "rr": {"f": "weekly"},
                "rp": None,
                "rt": [],
            },
            "server_index": 1,
        },
        {
            "uuid": hidden_child,
            "e": KIND_TASK7,
            "t": ACTION_CREATED,
            "p": {
                "tt": "Child of finished project",
                "tp": 0,
                "st": ST_ANYTIME,
                "ss": SS_PENDING,
                "sr": _date_to_ts(today),
                "pr": [completed_project],
            },
            "server_index": 2,
        },
    ]

    tasks = _rebuild_state(items)

    assert filter_tasks(tasks, list_name="inbox") == []
    assert filter_tasks(tasks, list_name="today") == []
    assert next(task for task in tasks if task["uuid"] == recurring_uuid)[
        "recurring"
    ]
    assert not next(task for task in tasks if task["uuid"] == hidden_child)[
        "ancestors_available"
    ]
