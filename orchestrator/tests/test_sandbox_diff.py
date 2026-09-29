"""M2.4: `_diff` is the zero-I/O core of SandboxProvider.reconcile() -- plain dicts/dataclasses
in, a list of SandboxDrift out. No DB, no subprocess; this is where reconcile's branch
coverage should live (see orchestrator/src/orchestrator/sandbox/local.py's module docstring)."""
import uuid

from orchestrator.sandbox.local import SandboxRow, _diff
from orchestrator.sandbox.provider import SandboxDrift


def test_row_live_and_matching_is_synced():
    task_id = uuid.uuid4()
    rows = [SandboxRow(task_id=task_id, name="sbx-a", status="running")]
    live = {"sbx-a": "running"}

    assert _diff(live, rows) == [
        SandboxDrift(task_id=task_id, name="sbx-a", db_status="running",
                      live_status="running", resolution="synced"),
    ]


def test_row_live_but_status_differs_is_still_synced_with_live_status_reported():
    task_id = uuid.uuid4()
    rows = [SandboxRow(task_id=task_id, name="sbx-a", status="creating")]
    live = {"sbx-a": "running"}

    [drift] = _diff(live, rows)
    assert drift.resolution == "synced"
    assert drift.db_status == "creating"
    assert drift.live_status == "running"


def test_row_with_no_live_entry_is_marked_removed():
    task_id = uuid.uuid4()
    rows = [SandboxRow(task_id=task_id, name="sbx-gone", status="running")]

    assert _diff({}, rows) == [
        SandboxDrift(task_id=task_id, name="sbx-gone", db_status="running",
                      live_status=None, resolution="marked_removed"),
    ]


def test_live_entry_with_no_row_is_orphan_reported():
    live = {"sbx-orphan": "running"}

    assert _diff(live, []) == [
        SandboxDrift(task_id=None, name="sbx-orphan", db_status=None,
                      live_status="running", resolution="orphan_reported"),
    ]


def test_mixed_drift_covers_all_three_resolutions_in_one_pass():
    task_a, task_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        SandboxRow(task_id=task_a, name="sbx-synced", status="running"),
        SandboxRow(task_id=task_b, name="sbx-gone", status="running"),
    ]
    live = {"sbx-synced": "running", "sbx-orphan": "running"}

    drifts = {d.name: d for d in _diff(live, rows)}
    assert drifts["sbx-synced"].resolution == "synced"
    assert drifts["sbx-gone"].resolution == "marked_removed"
    assert drifts["sbx-orphan"].resolution == "orphan_reported"
    assert len(drifts) == 3
