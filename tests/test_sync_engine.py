from __future__ import annotations

import copy

import pytest
import requests

from sync_engine import (
    SafeThings,
    SyncError,
    build_task7_create_payload,
    validate_outbound_items,
)
from things_cloud import (
    ACTION_CREATED,
    ACTION_MODIFIED,
    KIND_AREA,
    SS_CANCELED,
    SS_COMPLETED,
    ST_ANYTIME,
    ST_INBOX,
    ST_SOMEDAY,
    ThingsConflictError,
    _date_to_ts,
    _resolve_when,
    new_uuid,
)


class FakeCloud:
    def __init__(self):
        self.head = 0
        self.items: list[dict] = []
        self.offline = False
        self.commits: list[dict] = []

    def verify(self):
        if self.offline:
            raise requests.ConnectionError("offline")
        return {"history-key": "test-history"}

    def get_history_head(self, history_key):
        if self.offline:
            raise requests.ConnectionError("offline")
        return {"latest-server-index": self.head}

    def sync_history_items(self, history_key, start_index=0):
        if self.offline:
            raise requests.ConnectionError("offline")
        return (
            [
                copy.deepcopy(item)
                for item in self.items
                if int(item.get("server_index", 0)) >= int(start_index)
            ],
            self.head,
        )

    def commit_once(self, history_key, items, ancestor_index):
        if self.offline:
            raise requests.ConnectionError("offline")
        if ancestor_index != self.head:
            raise ThingsConflictError(
                "stale ancestor", latest_server_index=self.head
            )
        validate_outbound_items(items)
        self.commits.append(
            {
                "ancestor": ancestor_index,
                "items": copy.deepcopy(items),
            }
        )
        self._append(items)
        return self.head

    def external_commit(self, items):
        if self.offline:
            raise requests.ConnectionError("offline")
        self._append(items)
        return self.head

    def _append(self, items):
        index = self.head
        for item in items:
            copied = copy.deepcopy(item)
            copied["server_index"] = index
            self.items.append(copied)
        self.head += 1


def task_payload(
    title: str,
    *,
    st: int = ST_INBOX,
    tp: int = 0,
    **overrides,
):
    when = "inbox" if st == ST_INBOX else "anytime"
    payload = build_task7_create_payload(
        title=title,
        task_type=tp,
        when=when,
        now=1_770_000_000,
    )
    payload.update(overrides)
    return payload


def seed_task(cloud: FakeCloud, title="Base task", **overrides):
    task_uuid = new_uuid()
    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task7",
                "t": ACTION_CREATED,
                "p": task_payload(title, **overrides),
            }
        ]
    )
    return task_uuid


def make_engine(cloud: FakeCloud, tmp_path):
    return SafeThings(
        "test@example.com",
        "secret",
        "test-history",
        client=cloud,
        data_dir=str(tmp_path),
    )


def test_task7_create_payload_is_complete_and_canonical():
    payload = task_payload("Complete payload")
    assert len(payload) == 34
    assert payload["md"] is None
    assert payload["rr"] is None
    assert payload["rp"] is None
    assert payload["rt"] == []
    assert payload["ss"] == 0
    assert payload["nt"]["ch"] != 0 or payload["nt"]["v"] == ""
    validate_outbound_items(
        [
            {
                "uuid": new_uuid(),
                "e": "Task7",
                "t": ACTION_CREATED,
                "p": payload,
            }
        ]
    )


def test_create_task_with_tag_syncs_and_projects(tmp_path):
    cloud = FakeCloud()
    engine = make_engine(cloud, tmp_path)

    task_uuid = engine.add_task(
        "Ship Windows client",
        notes="A note",
        when="today",
        deadline="2030-01-02",
        tags=["work", "urgent"],
    )

    task = next(item for item in engine.get_all_tasks() if item["uuid"] == task_uuid)
    assert task["title"] == "Ship Windows client"
    assert task["notes"] == "A note"
    assert task["deadline_date"] == "2030-01-02"
    assert sorted(task["tags"]) == ["urgent", "work"]
    assert engine.sync_status()["pending"] == 0

    committed = cloud.commits[-1]["items"]
    task_item = next(item for item in committed if item["uuid"] == task_uuid)
    assert task_item["e"] == "Task7"
    assert len(task_item["p"]) == 34
    assert any(item["e"] == "Tag4" for item in committed)


def test_project_and_child_task_round_trip(tmp_path):
    cloud = FakeCloud()
    area_uuid = new_uuid()
    cloud.external_commit(
        [
            {
                "uuid": area_uuid,
                "e": KIND_AREA,
                "t": ACTION_CREATED,
                "p": {"tt": "Work"},
            }
        ]
    )
    engine = make_engine(cloud, tmp_path)

    project_uuid = engine.add_project(
        "Release",
        notes="Project notes",
        area_uuid=area_uuid,
        tags=["roadmap"],
    )
    child_uuid = engine.add_task(
        "Package installer",
        project_uuid=project_uuid,
        deadline="2030-02-03",
    )

    project = next(
        item for item in engine.get_projects() if item["uuid"] == project_uuid
    )
    child = next(
        item for item in engine.get_all_tasks() if item["uuid"] == child_uuid
    )
    assert project["type"] == "project"
    assert project["area_name"] == "Work"
    assert child["project_name"] == "Release"
    assert child["st_code"] == ST_ANYTIME
    assert child["deadline_date"] == "2030-02-03"


def test_remote_change_is_visible_without_restart(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Before")
    engine = make_engine(cloud, tmp_path)
    assert engine.get_all_tasks()[0]["title"] == "Before"

    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task7",
                "t": ACTION_MODIFIED,
                "p": {"tt": "After", "md": 1_770_000_100},
            }
        ]
    )

    task = next(item for item in engine.get_all_tasks() if item["uuid"] == task_uuid)
    assert task["title"] == "After"


def test_edit_notes_tags_deadline_and_title(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Draft")
    engine = make_engine(cloud, tmp_path)

    result = engine.modify_task_fields(
        task_uuid,
        {
            "tt": "Final",
            "nt": "Unicode α 🚀",
            "tg": ["work"],
            "dd": "2030-04-05",
        },
    )

    assert result["ok"] is True
    task = next(item for item in engine.get_all_tasks() if item["uuid"] == task_uuid)
    assert task["title"] == "Final"
    assert task["notes"] == "Unicode α 🚀"
    assert task["tags"] == ["work"]
    assert task["deadline_date"] == "2030-04-05"
    assert cloud.commits[-1]["items"][-1]["e"] == "Task7"


def test_status_trash_and_restore(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Lifecycle")
    engine = make_engine(cloud, tmp_path)

    assert engine.set_status(task_uuid, "completed") is True
    assert cloud.commits[-1]["items"][0]["p"]["ss"] == SS_COMPLETED
    assert engine.set_status(task_uuid, "pending") is True
    assert cloud.commits[-1]["items"][0]["p"]["ss"] == 0
    assert engine.set_status(task_uuid, "canceled") is True
    assert cloud.commits[-1]["items"][0]["p"]["ss"] == SS_CANCELED
    assert cloud.commits[-1]["items"][0]["e"] == "Task6"

    assert engine.trash_task(task_uuid) is True
    assert any(
        item["uuid"] == task_uuid and item["trashed"]
        for item in engine.get_tasks("trash")
    )
    assert engine.trash_task(task_uuid, trashed=False) is True
    assert not any(
        item["uuid"] == task_uuid and item["trashed"]
        for item in engine.get_all_tasks()
    )


def test_legacy_task6_remains_editable(tmp_path):
    cloud = FakeCloud()
    task_uuid = new_uuid()
    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task6",
                "t": ACTION_CREATED,
                "p": {"tt": "Legacy", "st": 0, "ss": 0},
            }
        ]
    )
    engine = make_engine(cloud, tmp_path)
    engine.modify_task_fields(task_uuid, {"tt": "Legacy edited"})
    assert cloud.commits[-1]["items"][0]["e"] == "Task6"
    assert cloud.commits[-1]["items"][0]["p"]["tt"] == "Legacy edited"


def test_repeating_task_edit_is_rejected_before_commit(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(
        cloud,
        "Weekly",
        rr={"f": 1},
        rp=None,
        rt=[],
    )
    engine = make_engine(cloud, tmp_path)
    commits_before = len(cloud.commits)

    with pytest.raises(SyncError, match="重复任务"):
        engine.modify_task_fields(task_uuid, {"tt": "Unsafe"})
    assert len(cloud.commits) == commits_before


def test_invalid_uuid_and_incomplete_payload_are_rejected_before_post():
    invalid = task_payload("Unsafe")
    invalid.pop("md")
    with pytest.raises(SyncError, match="UUID"):
        validate_outbound_items(
            [
                {
                    "uuid": "not-a-things-uuid",
                    "e": "Task7",
                    "t": ACTION_CREATED,
                    "p": task_payload("Unsafe"),
                }
            ]
        )
    with pytest.raises(SyncError, match="字段不完整"):
        validate_outbound_items(
            [
                {
                    "uuid": new_uuid(),
                    "e": "Task7",
                    "t": ACTION_CREATED,
                    "p": invalid,
                }
            ]
        )


def test_offline_outbox_survives_restart_and_flushes_once(tmp_path):
    cloud = FakeCloud()
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True
    task_uuid = engine.add_task("Offline task", notes="still here")
    assert engine.sync_status()["pending"] == 1
    assert engine.sync_status()["online"] is False

    restarted = make_engine(cloud, tmp_path)
    assert restarted.sync_status()["pending"] == 1
    assert any(
        task["uuid"] == task_uuid and task["title"] == "Offline task"
        for task in restarted.get_all_tasks()
    )

    cloud.offline = False
    restarted.flush()
    assert restarted.sync_status()["pending"] == 0
    commits_after_first_flush = len(cloud.commits)
    restarted.flush()
    assert len(cloud.commits) == commits_after_first_flush
    assert sum(item["uuid"] == task_uuid for item in cloud.items) == 1


def test_offline_write_preserves_unrelated_remote_edit(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Base", nt={"unused": True})
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True
    engine.modify_task_fields(task_uuid, {"tt": "Local title"})
    cloud.offline = False

    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task7",
                "t": ACTION_MODIFIED,
                "p": {
                    "nt": {
                        "_t": "tx",
                        "t": 1,
                        "ch": 0,
                        "v": "Remote note",
                    },
                    "md": 1_770_000_200,
                },
            }
        ]
    )

    engine.flush()
    task = next(item for item in engine.get_all_tasks() if item["uuid"] == task_uuid)
    assert task["title"] == "Local title"
    assert task["notes"] == "Remote note"
    assert engine.get_conflicts() == []


def test_same_field_conflict_is_visible_and_retryable(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Base")
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True
    engine.modify_task_fields(task_uuid, {"tt": "Windows title"})
    cloud.offline = False

    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task7",
                "t": ACTION_MODIFIED,
                "p": {"tt": "iPhone title", "md": 1_770_000_300},
            }
        ]
    )

    engine.flush()
    conflicts = engine.get_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["uuid"] == task_uuid
    assert conflicts[0]["fields"] == ["tt"]
    assert next(
        item for item in engine.get_all_tasks() if item["uuid"] == task_uuid
    )["title"] == "iPhone title"

    engine.resolve_conflict(conflicts[0]["id"], retry=True)
    assert engine.get_conflicts() == []
    assert next(
        item for item in engine.get_all_tasks() if item["uuid"] == task_uuid
    )["title"] == "Windows title"


def test_two_offline_edits_to_same_field_converge_to_latest(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Base")
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True
    engine.modify_task_fields(task_uuid, {"tt": "First local"})
    engine.modify_task_fields(task_uuid, {"tt": "Latest local"})
    cloud.offline = False

    engine.flush()
    assert engine.get_conflicts() == []
    assert next(
        item for item in engine.get_all_tasks() if item["uuid"] == task_uuid
    )["title"] == "Latest local"


def test_offline_create_then_complete_keeps_create_and_status_separate(tmp_path):
    cloud = FakeCloud()
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True

    task_uuid = engine.add_task("Create while offline")
    engine.set_status(task_uuid, "completed")
    assert engine.sync_status()["pending"] == 2

    restarted = make_engine(cloud, tmp_path)
    cloud.offline = False
    restarted.flush()

    assert len(cloud.commits) == 2
    first_task = next(
        item for item in cloud.commits[0]["items"] if item["uuid"] == task_uuid
    )
    second_task = next(
        item for item in cloud.commits[1]["items"] if item["uuid"] == task_uuid
    )
    assert first_task["t"] == ACTION_CREATED
    assert first_task["e"] == "Task7"
    assert len(first_task["p"]) == 34
    assert first_task["p"]["ss"] == 0
    assert second_task["t"] == ACTION_MODIFIED
    assert second_task["p"]["ss"] == SS_COMPLETED
    assert restarted.sync_status()["pending"] == 0
    assert next(
        item
        for item in restarted.get_all_tasks()
        if item["uuid"] == task_uuid
    )["human_status"] == "completed"


def test_offline_create_then_edit_keeps_create_canonical(tmp_path):
    cloud = FakeCloud()
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True

    task_uuid = engine.add_task("Draft offline")
    engine.modify_task_fields(
        task_uuid,
        {"tt": "Edited offline", "nt": "Created and edited without network"},
    )
    cloud.offline = False
    engine.flush()

    assert len(cloud.commits) == 2
    create_item = next(
        item for item in cloud.commits[0]["items"] if item["uuid"] == task_uuid
    )
    edit_item = next(
        item for item in cloud.commits[1]["items"] if item["uuid"] == task_uuid
    )
    assert create_item["p"]["tt"] == "Draft offline"
    assert create_item["p"]["md"] is None
    assert len(create_item["p"]) == 34
    assert edit_item["t"] == ACTION_MODIFIED
    task = next(
        item for item in engine.get_all_tasks() if item["uuid"] == task_uuid
    )
    assert task["title"] == "Edited offline"
    assert task["notes"] == "Created and edited without network"


@pytest.mark.parametrize(
    ("target", "expected_st", "expected_date"),
    [
        ("inbox", ST_INBOX, None),
        ("today", ST_ANYTIME, "today"),
        ("tomorrow", ST_SOMEDAY, "tomorrow"),
        ("anytime", ST_ANYTIME, None),
        ("someday", ST_SOMEDAY, None),
        ("2031-05-06", ST_SOMEDAY, _date_to_ts("2031-05-06")),
    ],
)
def test_move_schedule_targets(tmp_path, target, expected_st, expected_date):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Movable")
    engine = make_engine(cloud, tmp_path)

    with engine._lock:
        engine.move_schedule(task_uuid, target)
        payload = cloud.commits[-1]["items"][0]["p"]

    assert payload["st"] == expected_st
    if expected_date == "today":
        expected_date = _resolve_when("today")[1]
    elif expected_date == "tomorrow":
        expected_date = _resolve_when("tomorrow")[1]
    assert payload["sr"] == expected_date
    assert payload["tir"] == expected_date


def test_unknown_schedule_target_is_rejected(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Movable")
    engine = make_engine(cloud, tmp_path)
    with pytest.raises(SyncError, match="未知目标"):
        engine.move_schedule(task_uuid, "next-thursday-ish")


def test_project_edit_and_area_movement(tmp_path):
    cloud = FakeCloud()
    first_area = new_uuid()
    second_area = new_uuid()
    cloud.external_commit(
        [
            {
                "uuid": first_area,
                "e": KIND_AREA,
                "t": ACTION_CREATED,
                "p": {"tt": "First area"},
            },
            {
                "uuid": second_area,
                "e": KIND_AREA,
                "t": ACTION_CREATED,
                "p": {"tt": "Second area"},
            },
        ]
    )
    engine = make_engine(cloud, tmp_path)
    project_uuid = engine.add_project("Project", area_uuid=first_area)

    engine.modify_task_fields(
        project_uuid,
        {
            "tt": "Renamed project",
            "nt": "Project notes",
            "dd": "2031-06-07",
            "area_uuid": second_area,
        },
    )
    project = next(
        item for item in engine.get_projects() if item["uuid"] == project_uuid
    )
    assert project["title"] == "Renamed project"
    assert project["notes"] == "Project notes"
    assert project["deadline_date"] == "2031-06-07"
    assert project["area_name"] == "Second area"
    assert cloud.commits[-1]["items"][-1]["e"] == "Task7"


def test_task_moves_between_project_and_area(tmp_path):
    cloud = FakeCloud()
    area_uuid = new_uuid()
    cloud.external_commit(
        [
            {
                "uuid": area_uuid,
                "e": KIND_AREA,
                "t": ACTION_CREATED,
                "p": {"tt": "Area"},
            }
        ]
    )
    engine = make_engine(cloud, tmp_path)
    project_uuid = engine.add_project("Project", area_uuid=area_uuid)
    child_uuid = engine.add_task("Child", project_uuid=project_uuid)

    engine.modify_task_fields(
        child_uuid,
        {"project_uuid": None, "area_uuid": area_uuid},
    )
    child = next(
        item for item in engine.get_all_tasks() if item["uuid"] == child_uuid
    )
    assert child["project_uuids"] == []
    assert child["area_name"] == "Area"

    engine.modify_task_fields(
        child_uuid,
        {"project_uuid": project_uuid, "area_uuid": None},
    )
    child = next(
        item for item in engine.get_all_tasks() if item["uuid"] == child_uuid
    )
    assert child["project_name"] == "Project"
    assert child["area_uuids"] == []


def test_cannot_assign_item_to_itself(tmp_path):
    cloud = FakeCloud()
    engine = make_engine(cloud, tmp_path)
    project_uuid = engine.add_project("No cycles")
    task_uuid = engine.add_task("Child", project_uuid=project_uuid)

    with pytest.raises(SyncError, match="自己的子项"):
        engine.modify_task_fields(
            task_uuid,
            {"project_uuid": task_uuid, "area_uuid": None},
        )

    with pytest.raises(SyncError, match="自己的子项"):
        engine.modify_task_fields(
            project_uuid,
            {"project_uuid": project_uuid, "area_uuid": None},
        )


def test_batch_trash_deduplicates_and_prevalidates_whole_batch(tmp_path):
    cloud = FakeCloud()
    first_uuid = seed_task(cloud, "First")
    second_uuid = seed_task(cloud, "Second")
    engine = make_engine(cloud, tmp_path)

    removed = engine.trash_tasks_batch(
        [first_uuid, first_uuid, second_uuid, second_uuid]
    )
    assert removed == 2
    trashed = {task["uuid"] for task in engine.get_tasks("trash")}
    assert trashed == {first_uuid, second_uuid}

    third_uuid = seed_task(cloud, "Third")
    with pytest.raises(SyncError):
        engine.trash_tasks_batch([third_uuid, "bad-uuid"])
    third = next(
        item for item in engine.get_all_tasks() if item["uuid"] == third_uuid
    )
    assert third["trashed"] is False


def test_conflict_can_be_abandoned(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Base")
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True
    engine.modify_task_fields(task_uuid, {"tt": "Local title"})
    cloud.offline = False
    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task7",
                "t": ACTION_MODIFIED,
                "p": {"tt": "Remote title", "md": 1_770_000_400},
            }
        ]
    )

    engine.flush()
    conflict = engine.get_conflicts()[0]
    engine.resolve_conflict(conflict["id"], retry=False)

    assert engine.get_conflicts() == []
    task = next(
        item for item in engine.get_all_tasks() if item["uuid"] == task_uuid
    )
    assert task["title"] == "Remote title"


def test_conflicts_survive_restart(tmp_path):
    cloud = FakeCloud()
    task_uuid = seed_task(cloud, "Base")
    engine = make_engine(cloud, tmp_path)
    cloud.offline = True
    engine.modify_task_fields(task_uuid, {"tt": "Local title"})
    cloud.offline = False
    cloud.external_commit(
        [
            {
                "uuid": task_uuid,
                "e": "Task7",
                "t": ACTION_MODIFIED,
                "p": {"tt": "Remote title", "md": 1_770_000_500},
            }
        ]
    )
    engine.flush()
    conflict_id = engine.get_conflicts()[0]["id"]

    restarted = make_engine(cloud, tmp_path)
    conflicts = restarted.get_conflicts()
    assert [item["id"] for item in conflicts] == [conflict_id]
    assert conflicts[0]["fields"] == ["tt"]
