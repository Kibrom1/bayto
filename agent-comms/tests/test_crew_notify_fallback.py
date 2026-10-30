"""Tests for the crew-notify-fallback script (agent-comms/src/acp/crew_notify_fallback.py).

This script is installed by copying the single file into a sandbox's bin/,
so it must stay stdlib-only and schema-compatible with handoff_compat.py's
on-disk $FACTORY_DIR layout: a fallback reply must land in messages/*.json
with the full envelope real readers (handoff_compat._inbox_paths /
_print_messages) index directly, and must share handoff's seq counter in
state.json rather than recomputing it by scanning the factory tree.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest


def _write_state(factory_dir: Path, **overrides) -> None:
    (factory_dir / "claims").mkdir(parents=True, exist_ok=True)
    (factory_dir / "messages").mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": "r1", "task_id": "wad-999", "attempt": 1, "stage": "implementing",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "seq": 0,
    }
    state.update(overrides)
    (factory_dir / "state.json").write_text(json.dumps(state))


def _write_assignment(factory_dir: Path, to: str, frm: str, seq: int, body: str = "do the thing") -> None:
    msg = {
        "message_id": f"msg-r1-{seq:04d}-abc123", "run_id": "r1", "task_id": "wad-999",
        "attempt": 1, "seq": seq, "from": frm, "to": to, "kind": "assignment",
        "created_at": "2026-01-01T00:00:00Z", "requires_ack": True, "refs": {}, "body": body,
    }
    (factory_dir / "messages" / f"2026010100000{seq}-{seq:04d}-{msg['message_id']}.json").write_text(
        json.dumps(msg)
    )


@pytest.fixture
def mod(monkeypatch, tmp_path):
    factory_dir = tmp_path / "factory"
    monkeypatch.setenv("FACTORY_DIR", str(factory_dir))
    sys.modules.pop("acp.crew_notify_fallback", None)
    m = importlib.import_module("acp.crew_notify_fallback")
    importlib.reload(m)
    m.FACTORY_DIR = factory_dir
    return m


def _run_main(mod, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    try:
        mod.main()
    except SystemExit as e:
        return e.code
    return 0


def test_no_assignment(mod, monkeypatch, tmp_path):
    factory_dir = tmp_path / "factory"
    _write_state(factory_dir)

    code = _run_main(mod, monkeypatch, ["crew-notify-fallback", "developer"])

    assert code == 0
    assert list((factory_dir / "messages").glob("*.json")) == []


def test_assignment_delivers_reply_to_sender(mod, monkeypatch, tmp_path):
    factory_dir = tmp_path / "factory"
    _write_state(factory_dir)
    _write_assignment(factory_dir, to="developer", frm="coordinator", seq=1)

    monkeypatch.setattr(mod, "_call_ollama", lambda url, model, prompt: "the fallback answer")

    code = _run_main(mod, monkeypatch, ["crew-notify-fallback", "developer"])
    assert code == 0

    written = list((factory_dir / "messages").glob("*fallback*.json"))
    assert len(written) == 1
    data = json.loads(written[0].read_text())

    assert written[0].parent == factory_dir / "messages"
    assert data["to"] == "coordinator"
    assert data["from"] == "developer"
    assert data["kind"] == "note"
    assert data["body"] == "the fallback answer"
    for field in ("message_id", "created_at", "attempt", "refs", "run_id", "task_id", "requires_ack"):
        assert field in data


def test_seq_shares_handoff_counter(mod, monkeypatch, tmp_path):
    factory_dir = tmp_path / "factory"
    _write_state(factory_dir, seq=5)
    _write_assignment(factory_dir, to="developer", frm="coordinator", seq=1)

    monkeypatch.setattr(mod, "_call_ollama", lambda url, model, prompt: "ok")

    code = _run_main(mod, monkeypatch, ["crew-notify-fallback", "developer"])
    assert code == 0

    written = list((factory_dir / "messages").glob("*fallback*.json"))
    data = json.loads(written[0].read_text())
    assert data["seq"] == 6

    state = json.loads((factory_dir / "state.json").read_text())
    assert state["seq"] == 6


def test_ollama_error_exits_one_and_writes_nothing(mod, monkeypatch, tmp_path):
    factory_dir = tmp_path / "factory"
    _write_state(factory_dir)
    _write_assignment(factory_dir, to="developer", frm="coordinator", seq=1)

    def boom(url, model, prompt):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(mod, "_call_ollama", boom)

    code = _run_main(mod, monkeypatch, ["crew-notify-fallback", "developer"])

    assert code == 1
    # only the assignment we planted is there: no reply was written, and the seq counter did not move
    assert len(list((factory_dir / "messages").glob("*.json"))) == 1
    assert json.loads((factory_dir / "state.json").read_text())["seq"] == 0


def test_no_state_file_exits_zero_and_writes_nothing(mod, monkeypatch, tmp_path):
    factory_dir = tmp_path / "factory"
    (factory_dir / "messages").mkdir(parents=True, exist_ok=True)
    _write_assignment(factory_dir, to="developer", frm="coordinator", seq=1)
    # No state.json written: handoff was never initialised.

    monkeypatch.setattr(mod, "_call_ollama", lambda url, model, prompt: "ok")

    code = _run_main(mod, monkeypatch, ["crew-notify-fallback", "developer"])

    assert code == 0
    assert list((factory_dir / "messages").glob("*fallback*.json")) == []


def test_atomic_write_json_fsyncs_before_rename(mod, monkeypatch, tmp_path):
    """Must fsync the temp file before the rename, matching transport.py's
    _atomic_write and handoff_compat._atomic_write_text, so a reply already
    "written" to messages/ cannot be lost (truncated/missing) on a crash
    or sandbox stop right after the rename.
    """
    fsynced = []
    real_fsync = os.fsync
    monkeypatch.setattr(mod.os, "fsync", lambda fd: (fsynced.append(fd), real_fsync(fd))[1])

    target = tmp_path / "out.json"
    mod._atomic_write_json(target, {"a": 1})

    assert fsynced, "_atomic_write_json did not call os.fsync before renaming into place"
    assert json.loads(target.read_text()) == {"a": 1}
