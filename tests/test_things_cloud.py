from __future__ import annotations

import base64
import json

import pytest

from things_cloud import (
    ACTION_CREATED,
    KIND_TASK7,
    THINGS_APP_ID,
    THINGS_USER_AGENT,
    ThingsConflictError,
    ThingsClient,
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
