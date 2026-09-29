"""M1.5 compatibility check: the acp-backed handoff shim vs. the workshop's
original bash `handoff`.

Every test in this file is parametrized over both implementations and asserts
the *same* contract for each: CLI surface (subcommands, flags, exit codes) and
on-disk $FACTORY_DIR layout (directories, message/report JSON schema, claim
and stage semantics). Passing for both is the proof that swapping one for the
other is a no-behavior-change substitution, as required by docs/work-plan.md
M1.5.

The bash reference needs `jq` on PATH (an existing dependency of the vendor
script, not of the shim); those cases are skipped, not failed, if it is
missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASH_HANDOFF = REPO_ROOT / "vendor/wad-sbx-workshop/chapters/support/bin/handoff"
PY_HANDOFF = REPO_ROOT / "agent-comms/src/acp/handoff_compat.py"

HAVE_JQ = shutil.which("jq") is not None

IMPLS = [
    pytest.param([str(BASH_HANDOFF)], id="bash-reference",
                 marks=pytest.mark.skipif(not HAVE_JQ, reason="reference handoff needs jq")),
    pytest.param([sys.executable, str(PY_HANDOFF)], id="acp-shim"),
]


@pytest.fixture(params=IMPLS)
def handoff(request, tmp_path):
    prefix = request.param
    factory_dir = tmp_path / "factory"
    env = {**os.environ, "FACTORY_DIR": str(factory_dir)}

    def run(*args, check=False):
        p = subprocess.run([*prefix, *args], env=env, text=True,
                            capture_output=True, timeout=30)
        if check and p.returncode != 0:
            raise AssertionError(f"{args} exited {p.returncode}\nstdout={p.stdout}\nstderr={p.stderr}")
        return p

    run.factory_dir = factory_dir
    run.prefix = prefix
    return run


def _init(handoff, run="r1", task="wad-999"):
    p = handoff("init", "--run", run, "--task", task)
    assert p.returncode == 0
    return p


# ---------------------------------------------------------------- init / layout

def test_init_creates_expected_layout(handoff):
    _init(handoff)
    fd = handoff.factory_dir
    for d in ("messages", "reports", "claims", "decisions", "task", "evidence"):
        assert (fd / d).is_dir(), d
    for role in ("coordinator", "developer", "qa", "human", "supervisor"):
        assert (fd / "consumed" / role).is_dir(), role
    state = json.loads((fd / "state.json").read_text())
    assert state["run_id"] == "r1"
    assert state["task_id"] == "wad-999"
    assert state["attempt"] == 1
    assert state["stage"] == "queued"
    assert state["seq"] == 0


def test_init_is_idempotent_and_keeps_first_state(handoff):
    _init(handoff, run="r1", task="wad-999")
    p = handoff("init", "--run", "r2", "--task", "other")
    assert p.returncode == 0
    state = json.loads((handoff.factory_dir / "state.json").read_text())
    assert state["run_id"] == "r1"  # first init wins
    assert state["task_id"] == "wad-999"


def test_commands_require_init_first(handoff):
    p = handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "hi")
    assert p.returncode == 2
    assert "handoff:" in p.stderr


# ---------------------------------------------------------------- send validation

def test_send_rejects_unknown_role_and_kind(handoff):
    _init(handoff)
    p = handoff("send", "--to", "nobody", "--from", "coordinator", "--kind", "note", "--body", "hi")
    assert p.returncode == 2
    p = handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "gossip", "--body", "hi")
    assert p.returncode == 2
    p = handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note")
    assert p.returncode == 2  # no body


def test_send_writes_expected_message_schema(handoff):
    _init(handoff)
    p = handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "assignment", "--body", "do it")
    assert p.returncode == 0
    msg_id = p.stdout.strip()
    assert msg_id.startswith("msg-r1-0001-")

    files = list((handoff.factory_dir / "messages").glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["message_id"] == msg_id
    assert data["run_id"] == "r1" and data["task_id"] == "wad-999"
    assert data["attempt"] == 1 and data["seq"] == 1
    assert data["from"] == "coordinator" and data["to"] == "developer"
    assert data["kind"] == "assignment"
    assert data["requires_ack"] is True
    assert data["refs"] == {}
    assert data["body"] == "do it"
    assert "created_at" in data


def test_send_refs_only_include_provided_fields(handoff):
    _init(handoff)
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "assignment",
            "--body", "x", "--base-commit", "abc123")
    data = json.loads(next((handoff.factory_dir / "messages").glob("*.json")).read_text())
    assert data["refs"] == {"base_commit": "abc123"}


# ---------------------------------------------------------------- inbox / read / ack

def test_inbox_does_not_consume_but_read_does(handoff):
    _init(handoff)
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "one")

    peek1 = json.loads(handoff("inbox", "developer", "--json").stdout)
    peek2 = json.loads(handoff("inbox", "developer", "--json").stdout)
    assert len(peek1) == 1 and len(peek2) == 1  # peeking twice changes nothing

    read1 = json.loads(handoff("read", "developer", "--json").stdout)
    assert len(read1) == 1
    read2 = json.loads(handoff("read", "developer", "--json").stdout)
    assert read2 == []  # exactly-once: consumed after the first read


def test_read_marks_consumed_file_present(handoff):
    _init(handoff)
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "one")
    data = json.loads(handoff("read", "developer", "--json").stdout)[0]
    assert (handoff.factory_dir / "consumed" / "developer" / data["message_id"]).exists()


def test_broadcast_style_per_recipient_consumption(handoff):
    # Legacy handoff has single-recipient `to`, so "broadcast" is simulated by
    # sending the same note to each role independently; consumption for one
    # recipient must not affect another's inbox.
    _init(handoff)
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "hi")
    handoff("send", "--to", "qa", "--from", "coordinator", "--kind", "note", "--body", "hi")
    handoff("read", "developer")
    assert json.loads(handoff("inbox", "developer", "--json").stdout) == []
    assert len(json.loads(handoff("inbox", "qa", "--json").stdout)) == 1


def test_ack_is_idempotent(handoff):
    _init(handoff)
    p = handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "x")
    msg_id = p.stdout.strip()
    assert handoff("ack", "developer", msg_id).returncode == 0
    assert handoff("ack", "developer", msg_id).returncode == 0  # retried ack is not an error
    assert json.loads(handoff("inbox", "developer", "--json").stdout) == []


def test_kind_filter_on_inbox(handoff):
    _init(handoff)
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "n")
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "assignment", "--body", "a")
    only_assignment = json.loads(handoff("inbox", "developer", "--kind", "assignment", "--json").stdout)
    assert len(only_assignment) == 1 and only_assignment[0]["kind"] == "assignment"


# ---------------------------------------------------------------- wait

def test_wait_returns_immediately_when_message_present(handoff):
    _init(handoff)
    handoff("send", "--to", "developer", "--from", "coordinator", "--kind", "note", "--body", "x")
    p = handoff("wait", "--to", "developer", "--timeout", "5", "--interval", "1")
    assert p.returncode == 0
    assert p.stdout.strip()  # prints the message file path


def test_wait_times_out_with_exit_3(handoff):
    _init(handoff)
    p = handoff("wait", "--to", "developer", "--timeout", "1", "--interval", "1")
    assert p.returncode == 3


# ---------------------------------------------------------------- reports

def test_report_records_checks_and_latest_pointer(handoff):
    _init(handoff)
    p = handoff("report", "--from", "developer", "--status", "implemented",
                "--stage", "implementing", "--output-commit", "deadbeef",
                "--summary", "done", "--check", "pytest=0", "--check", "lint=0")
    assert p.returncode == 0
    report_id = p.stdout.strip()

    latest = json.loads(handoff("latest-report", "developer", "--json").stdout)
    assert latest["report_id"] == report_id
    assert latest["status"] == "implemented"
    assert latest["checks_passed"] is True
    assert {c["name"]: c["exit"] for c in latest["checks"]} == {"pytest": 0, "lint": 0}


def test_report_checks_passed_false_on_failing_check(handoff):
    _init(handoff)
    handoff("report", "--from", "developer", "--status", "implemented", "--check", "pytest=1")
    latest = json.loads(handoff("latest-report", "developer", "--json").stdout)
    assert latest["checks_passed"] is False


def test_report_checks_passed_false_with_no_checks(handoff):
    _init(handoff)
    handoff("report", "--from", "developer", "--status", "started")
    latest = json.loads(handoff("latest-report", "developer", "--json").stdout)
    assert latest["checks_passed"] is False


def test_latest_report_missing_returns_3(handoff):
    _init(handoff)
    p = handoff("latest-report", "developer", "--json")
    assert p.returncode == 3


def test_latest_report_stale_attempt_returns_4(handoff):
    _init(handoff)
    handoff("report", "--from", "developer", "--status", "implemented", "--check", "t=0")
    p = handoff("latest-report", "developer", "--attempt", "2", "--json")
    assert p.returncode == 4


def test_reports_listing_sorted_by_seq(handoff):
    _init(handoff)
    handoff("report", "--from", "developer", "--status", "started")
    handoff("report", "--from", "qa", "--status", "review-pass", "--check", "t=0")
    recs = json.loads(handoff("reports", "--json").stdout)
    assert [r["from"] for r in recs] == ["developer", "qa"]
    assert recs[0]["seq"] < recs[1]["seq"]


# ---------------------------------------------------------------- claims / stage

def test_claim_is_single_fire(handoff):
    _init(handoff)
    assert handoff("claim", "wake-msg-1-developer").returncode == 0
    assert handoff("claim", "wake-msg-1-developer").returncode == 9


def test_claim_rejects_bad_key(handoff):
    _init(handoff)
    assert handoff("claim", "not a valid key!").returncode == 2


def test_stage_get_and_set(handoff):
    _init(handoff)
    assert handoff("stage").stdout.strip() == "queued"
    p = handoff("stage", "implementing")
    assert p.returncode == 0 and p.stdout.strip() == "implementing"
    assert handoff("stage").stdout.strip() == "implementing"


def test_stage_rejects_unknown_value(handoff):
    _init(handoff)
    assert handoff("stage", "not-a-stage").returncode == 2


# ---------------------------------------------------------------- state / doctor

def test_state_prints_json(handoff):
    _init(handoff)
    state = json.loads(handoff("state").stdout)
    assert state["task_id"] == "wad-999"


def test_doctor_ok_after_init(handoff):
    _init(handoff)
    assert handoff("doctor").returncode == 0


def test_doctor_reports_problems_before_init(handoff):
    p = handoff("doctor")
    assert p.returncode == 1
    assert "MISS" in p.stdout


# ---------------------------------------------------------------- concurrency

def test_concurrent_sends_get_gapless_unique_seq(handoff):
    _init(handoff)
    n_per_writer, n_writers = 8, 3
    procs = []
    for i in range(n_writers):
        for j in range(n_per_writer):
            procs.append(subprocess.Popen(
                [*handoff.prefix, "send", "--to", "developer", "--from", "coordinator",
                 "--kind", "note", "--body", f"w{i}-{j}"],
                env={**os.environ, "FACTORY_DIR": str(handoff.factory_dir)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
    for p in procs:
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, err

    seqs = sorted(json.loads(f.read_text())["seq"]
                  for f in (handoff.factory_dir / "messages").glob("*.json"))
    assert seqs == list(range(1, n_per_writer * n_writers + 1))
