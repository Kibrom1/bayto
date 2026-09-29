#!/usr/bin/env python3
"""handoff (acp-backed) — drop-in replacement for the workshop's file-based
`handoff` command.

Same CLI surface, same $FACTORY_DIR on-disk layout, same exit codes as
chapters/support/bin/handoff in wad-sbx-workshop (see vendor/). This module
is part of the acp package (agent-comms/) but is deliberately stdlib-only,
so the exact same file can be copied into a team sandbox's bin/ directory
and run there without installing agent-comms' own dependencies (see M1.5 /
docs/decisions.md).

Usage (unchanged from the bash handoff):
  handoff init --run RUN --task TASK [--attempt N]
  handoff send --to ROLE --from ROLE --kind KIND [--body TEXT|--body-file F]
               [--attempt N] [--base-commit SHA] [--output-commit SHA] [--task-version V]
  handoff inbox ROLE [--kind KIND] [--json]
  handoff read ROLE [--kind KIND] [--json]
  handoff wait --to ROLE [--kind KIND] --timeout SECONDS [--interval SECONDS]
  handoff ack ROLE MESSAGE_ID
  handoff report --from ROLE --status STATUS [--stage STAGE] [--output-commit SHA]
                 [--attempt N] [--summary TEXT] [--body-file F] [--check NAME=EXIT]...
  handoff latest-report ROLE [--attempt N] [--json]
  handoff reports [--json]
  handoff claim KEY
  handoff stage [STAGE]
  handoff state
  handoff doctor
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROLES = ["coordinator", "developer", "qa"]
ALWAYS_ROLES = ["human", "supervisor"]
VALID_KINDS = [
    "assignment", "question", "answer", "review-request", "review-result",
    "report", "decision-request", "decision", "resume", "note",
]
VALID_STATUS = [
    "started", "implemented", "review-pass", "review-fail",
    "blocked-access", "needs-human", "failed", "acknowledged",
]
VALID_STAGES = [
    "queued", "assigned", "implementing", "review", "correcting",
    "blocked-access", "needs-human", "ready-for-acceptance", "accepted",
    "finished", "failed",
]

CLAIM_KEY_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,120}$")


class Die(Exception):
    """A fatal usage/validation error: always reported as exit code 2."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def rand6() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def in_list(needle: str, choices: list[str]) -> bool:
    return needle in choices


def _atomic_write_text(target: Path, data: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _atomic_write_json(target: Path, obj: dict) -> None:
    _atomic_write_text(target, json.dumps(obj, indent=2) + "\n")


class Factory:
    def __init__(self, root: Path):
        self.root = root
        self.messages = root / "messages"
        self.consumed = root / "consumed"
        self.reports = root / "reports"
        self.claims = root / "claims"
        self.state_file = root / "state.json"

    @classmethod
    def from_env(cls) -> "Factory":
        root = Path(os.environ.get("FACTORY_DIR", str(Path.home() / "work" / "factory")))
        return cls(root)

    def roles(self) -> list[str]:
        """Seats from $FACTORY_DIR/roster.json (written by start-team from team.tsv), else the workshop trio."""
        try:
            seats = json.loads((self.root / "roster.json").read_text())["roles"]
        except (OSError, ValueError, KeyError, TypeError):
            seats = DEFAULT_ROLES
        return list(seats) + ALWAYS_ROLES

    def require_init(self) -> None:
        if not self.state_file.exists():
            raise Die(f"no handoff directory at {self.root} (run: handoff init --run RUN --task TASK)")

    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def state_get(self, key: str) -> str:
        val = self.state().get(key, "")
        return "" if val is None else str(val)

    def state_set(self, key: str, value: str) -> None:
        st = self.state()
        st[key] = value
        st["updated_at"] = now()
        _atomic_write_json(self.state_file, st)

    def next_seq(self) -> int:
        # Serialised by a claim directory: two writers cannot take the same seq.
        lock = self.claims / ".seq.lock"
        self.claims.mkdir(parents=True, exist_ok=True)
        tries = 0
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                tries += 1
                if tries > 100:
                    raise Die(f"could not take the sequence lock at {lock}")
                time.sleep(0.05)
        try:
            st = self.state()
            seq = int(st.get("seq", 0)) + 1
            st["seq"] = seq
            st["updated_at"] = now()
            _atomic_write_json(self.state_file, st)
        finally:
            lock.rmdir()
        return seq


def fail(msg: str, code: int) -> int:
    print(f"handoff: {msg}", file=sys.stderr)
    return code


# ---------------------------------------------------------------- init

def cmd_init(fac: Factory, args: list[str]) -> int:
    run = task = ""
    attempt = "1"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--run":
            run, i = args[i + 1], i + 2
        elif a == "--task":
            task, i = args[i + 1], i + 2
        elif a == "--attempt":
            attempt, i = args[i + 1], i + 2
        else:
            raise Die(f"init: unexpected argument {a}")
    if not run:
        raise Die("init: --run is required")
    if not task:
        raise Die("init: --task is required")

    for d in (fac.messages, fac.reports, fac.claims, fac.root / "decisions",
              fac.root / "task", fac.root / "evidence"):
        d.mkdir(parents=True, exist_ok=True)
    for role in fac.roles():
        (fac.consumed / role).mkdir(parents=True, exist_ok=True)

    if fac.state_file.exists():
        print(f"handoff already initialised for run {fac.state_get('run_id')}", file=sys.stderr)
        return 0

    ts = now()
    _atomic_write_json(fac.state_file, {
        "run_id": run, "task_id": task, "attempt": int(attempt), "stage": "queued",
        "created_at": ts, "updated_at": ts, "seq": 0,
    })
    print(f"initialised handoff for run {run} task {task} attempt {attempt} at {fac.root}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- send

def cmd_send(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    to = frm = kind = body = body_file = attempt = base = output = version = ""
    requires_ack = True
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--to":
            to, i = args[i + 1], i + 2
        elif a == "--from":
            frm, i = args[i + 1], i + 2
        elif a == "--kind":
            kind, i = args[i + 1], i + 2
        elif a == "--body":
            body, i = args[i + 1], i + 2
        elif a == "--body-file":
            body_file, i = args[i + 1], i + 2
        elif a == "--attempt":
            attempt, i = args[i + 1], i + 2
        elif a == "--base-commit":
            base, i = args[i + 1], i + 2
        elif a == "--output-commit":
            output, i = args[i + 1], i + 2
        elif a == "--task-version":
            version, i = args[i + 1], i + 2
        elif a == "--no-ack":
            requires_ack, i = False, i + 1
        else:
            raise Die(f"send: unexpected argument {a}")

    if not in_list(to, fac.roles()):
        raise Die(f"send: --to must be one of: {' '.join(fac.roles())}")
    if not in_list(frm, fac.roles()):
        raise Die(f"send: --from must be one of: {' '.join(fac.roles())}")
    if not in_list(kind, VALID_KINDS):
        raise Die(f"send: --kind must be one of: {' '.join(VALID_KINDS)}")
    if body_file:
        p = Path(body_file)
        if not p.is_file():
            raise Die(f"send: body file not found: {body_file}")
        body = p.read_text().rstrip("\n")
    if not body:
        raise Die("send: --body or --body-file is required")
    if not attempt:
        attempt = fac.state_get("attempt")

    run = fac.state_get("run_id")
    task = fac.state_get("task_id")
    seq = fac.next_seq()
    msg_id = f"msg-{run}-{seq:04d}-{rand6()}"
    created = now()
    refs = {}
    if base:
        refs["base_commit"] = base
    if output:
        refs["output_commit"] = output
    if version:
        refs["task_version"] = version

    msg = {
        "message_id": msg_id, "run_id": run, "task_id": task, "attempt": int(attempt),
        "seq": seq, "from": frm, "to": to, "kind": kind, "created_at": created,
        "requires_ack": requires_ack, "refs": refs, "body": body,
    }
    path = fac.messages / f"{stamp()}-{seq:04d}-{msg_id}.json"
    _atomic_write_json(path, msg)
    print(msg_id)
    return 0


# ---------------------------------------------------------------- inbox / read / ack

def _inbox_paths(fac: Factory, role: str, kind: str = "") -> list[Path]:
    if not fac.messages.is_dir():
        return []
    out = []
    for f in sorted(fac.messages.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("to") != role:
            continue
        if kind and data.get("kind") != kind:
            continue
        if (fac.consumed / role / data["message_id"]).exists():
            continue
        out.append(f)
    return out


def _print_messages(paths: list[Path]) -> None:
    for f in paths:
        data = json.loads(f.read_text())
        print(f"--- {data['message_id']}  {data['kind']}  {data['from']} -> {data['to']}"
              f"  attempt {data['attempt']}  {data['created_at']}")
        for k, v in data.get("refs", {}).items():
            print(f"    {k}: {v}")
        print(data.get("body", ""))
        print()


def _parse_role_kind_json(fac: Factory, args: list[str], cmd: str) -> tuple[str, str, bool]:
    if not args or args[0].startswith("--"):
        raise Die(f"{cmd}: ROLE is required")
    role = args[0]
    if not in_list(role, fac.roles()):
        raise Die(f"{cmd}: unknown role {role}")
    kind = ""
    as_json = False
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--kind":
            kind, i = args[i + 1], i + 2
        elif a == "--json":
            as_json, i = True, i + 1
        else:
            raise Die(f"{cmd}: unexpected argument {a}")
    return role, kind, as_json


def cmd_inbox(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    role, kind, as_json = _parse_role_kind_json(fac, args, "inbox")
    paths = _inbox_paths(fac, role, kind)
    if as_json:
        print(json.dumps([json.loads(p.read_text()) for p in paths], indent=2))
    else:
        _print_messages(paths)
    return 0


def cmd_read(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    role, kind, as_json = _parse_role_kind_json(fac, args, "read")
    paths = _inbox_paths(fac, role, kind)
    if as_json:
        print(json.dumps([json.loads(p.read_text()) for p in paths], indent=2))
    else:
        _print_messages(paths)
    # Captured (and printed) before marking consumed: a crash before this point
    # redelivers the message rather than losing it.
    for f in paths:
        data = json.loads(f.read_text())
        d = fac.consumed / role
        d.mkdir(parents=True, exist_ok=True)
        (d / data["message_id"]).touch()
    return 0


def cmd_ack(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    if len(args) < 2:
        raise Die("ack: ROLE and MESSAGE_ID are required")
    role, msg_id = args[0], args[1]
    if not in_list(role, fac.roles()):
        raise Die(f"ack: unknown role {role}")
    d = fac.consumed / role
    d.mkdir(parents=True, exist_ok=True)
    (d / msg_id).touch()
    print(f"acked {msg_id} for {role}", file=sys.stderr)
    return 0


def cmd_wait(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    role = kind = ""
    timeout, interval = 120, 2
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--to":
            role, i = args[i + 1], i + 2
        elif a == "--kind":
            kind, i = args[i + 1], i + 2
        elif a == "--timeout":
            timeout, i = int(args[i + 1]), i + 2
        elif a == "--interval":
            interval, i = float(args[i + 1]), i + 2
        else:
            raise Die(f"wait: unexpected argument {a}")
    if not role:
        raise Die("wait: --to is required")

    deadline = time.monotonic() + timeout
    while True:
        paths = _inbox_paths(fac, role, kind)
        if paths:
            print(str(paths[0]))
            return 0
        if time.monotonic() >= deadline:
            return fail(f"timed out after {timeout}s waiting for a {kind or 'any'} message to {role}", 3)
        time.sleep(interval)


# ---------------------------------------------------------------- reports

def cmd_report(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    frm = status = stage = output = attempt = summary = body_file = ""
    checks: list[dict] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--from":
            frm, i = args[i + 1], i + 2
        elif a == "--status":
            status, i = args[i + 1], i + 2
        elif a == "--stage":
            stage, i = args[i + 1], i + 2
        elif a == "--output-commit":
            output, i = args[i + 1], i + 2
        elif a == "--attempt":
            attempt, i = args[i + 1], i + 2
        elif a == "--summary":
            summary, i = args[i + 1], i + 2
        elif a == "--body-file":
            body_file, i = args[i + 1], i + 2
        elif a == "--check":
            raw = args[i + 1]
            name, _, exit_s = raw.rpartition("=")
            if not name:
                raise Die("report: --check expects NAME=EXITCODE")
            checks.append({"name": name, "exit": int(exit_s) if exit_s else 1})
            i += 2
        else:
            raise Die(f"report: unexpected argument {a}")

    if not in_list(frm, fac.roles()):
        raise Die(f"report: --from must be one of: {' '.join(fac.roles())}")
    if not in_list(status, VALID_STATUS):
        raise Die(f"report: --status must be one of: {' '.join(VALID_STATUS)}")
    if not attempt:
        attempt = fac.state_get("attempt")
    body = ""
    if body_file:
        p = Path(body_file)
        if not p.is_file():
            raise Die(f"report: body file not found: {body_file}")
        body = p.read_text().rstrip("\n")

    run = fac.state_get("run_id")
    task = fac.state_get("task_id")
    seq = fac.next_seq()
    created = now()
    checks_passed = len(checks) > 0 and all(c["exit"] == 0 for c in checks)

    rec = {
        "report_id": f"rep-{run}-{seq}", "run_id": run, "task_id": task,
        "attempt": int(attempt), "seq": seq, "from": frm, "status": status, "stage": stage,
        "output_commit": output, "created_at": created, "summary": summary, "body": body,
        "checks": checks, "checks_passed": checks_passed,
    }
    path = fac.reports / f"{task}-a{attempt}-{frm}-{seq:04d}.json"
    _atomic_write_json(path, rec)

    # latest-<role>.json is a convenience pointer, written after the report itself so a
    # reader that finds it always finds the report too.
    latest = fac.reports / f"latest-{frm}.json"
    _atomic_write_text(latest, path.read_text())

    print(rec["report_id"])
    return 0


def cmd_latest_report(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    if not args or args[0].startswith("--"):
        raise Die("latest-report: ROLE is required")
    role = args[0]
    attempt = ""
    as_json = False
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--attempt":
            attempt, i = args[i + 1], i + 2
        elif a == "--json":
            as_json, i = True, i + 1
        else:
            raise Die(f"latest-report: unexpected argument {a}")
    if not attempt:
        attempt = fac.state_get("attempt")
    task = fac.state_get("task_id")

    newest = None
    newest_seq = -1
    for f in fac.reports.glob(f"{task}-a{attempt}-{role}-*.json"):
        data = json.loads(f.read_text())
        if data["seq"] > newest_seq:
            newest_seq, newest = data["seq"], f
    if newest is None:
        any_attempt = any(fac.reports.glob(f"{task}-a*-{role}-*.json"))
        if any_attempt:
            return fail(f"only stale reports found for {role} (no report for attempt {attempt})", 4)
        return fail(f"no report from {role} yet", 3)

    data = json.loads(newest.read_text())
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        checks_str = " ".join(f"{c['name']}={c['exit']}" for c in data["checks"])
        print(f"report {data['report_id']}\n"
              f"role: {data['from']}   status: {data['status']}   stage: {data['stage']}\n"
              f"attempt: {data['attempt']}   output_commit: {data['output_commit']}\n"
              f"checks: {checks_str}   checks_passed: {str(data['checks_passed']).lower()}\n\n"
              f"{data['summary']}\n\n{data['body']}")
    return 0


def cmd_reports(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    as_json = bool(args) and args[0] == "--json"
    files = [f for f in fac.reports.glob("*.json") if not f.name.startswith("latest-")]
    if not files:
        if as_json:
            print("[]")
        return 0
    recs = [json.loads(f.read_text()) for f in files]
    recs.sort(key=lambda r: r["seq"])
    if as_json:
        print(json.dumps(recs, indent=2))
    else:
        for r in sorted(recs, key=lambda r: r["created_at"]):
            print(f"{r['created_at']}  {r['from']}  {r['status']}  attempt={r['attempt']}"
                  f"  checks_passed={str(r['checks_passed']).lower()}  {r['output_commit']}")
    return 0


# ---------------------------------------------------------------- claims / stage

def cmd_claim(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    if not args:
        raise Die("claim: KEY is required")
    key = args[0]
    if not CLAIM_KEY_RE.match(key):
        raise Die("claim: KEY must be alphanumeric with . _ : -")
    fac.claims.mkdir(parents=True, exist_ok=True)
    d = fac.claims / key
    try:
        d.mkdir()
        (d / "claimed_at").write_text(now() + "\n")
        print(f"claimed {key}", file=sys.stderr)
        return 0
    except FileExistsError:
        claimed_at = "unknown"
        cf = d / "claimed_at"
        if cf.exists():
            claimed_at = cf.read_text().strip()
        return fail(f"{key} was already claimed at {claimed_at}", 9)


def cmd_stage(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    if not args:
        print(fac.state_get("stage"))
        return 0
    stage = args[0]
    if stage not in VALID_STAGES:
        raise Die(f"stage: unknown stage {stage}")
    fac.state_set("stage", stage)
    print(stage)
    return 0


def cmd_state(fac: Factory, args: list[str]) -> int:
    fac.require_init()
    print(fac.state_file.read_text(), end="")
    return 0


def cmd_doctor(fac: Factory, args: list[str]) -> int:
    problems = 0
    print(f"handoff directory: {fac.root}")
    for d in (fac.messages, fac.reports, fac.consumed, fac.claims):
        if d.is_dir():
            print(f"  ok   {d}")
        else:
            print(f"  MISS {d}")
            problems += 1

    if fac.state_file.exists():
        try:
            fac.state()
            print(f"  ok   state.json (run {fac.state_get('run_id')}, task {fac.state_get('task_id')}, "
                  f"attempt {fac.state_get('attempt')}, stage {fac.state_get('stage')})")
        except json.JSONDecodeError:
            print("  BAD  state.json is not valid JSON")
            problems += 1
    else:
        print("  MISS state.json")
        problems += 1

    bad = partial = 0
    for d in (fac.messages, fac.reports):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                json.loads(f.read_text())
            except json.JSONDecodeError:
                print(f"  BAD  {f} is not valid JSON")
                bad += 1
        for f in sorted(d.glob("*.tmp.*")):
            print(f"  WARN leftover temporary file {f}")
            partial += 1

    n_msg = len(list(fac.messages.glob("*.json"))) if fac.messages.is_dir() else 0
    n_rep = len(list(fac.reports.glob("*.json"))) if fac.reports.is_dir() else 0
    print(f"messages: {n_msg}   reports: {n_rep}   invalid: {bad}   leftover temp: {partial}")
    return 0 if (problems + bad) == 0 else 1


HELP = __doc__ or ""

DISPATCH = {
    "init": cmd_init,
    "send": cmd_send,
    "inbox": cmd_inbox,
    "read": cmd_read,
    "ack": cmd_ack,
    "wait": cmd_wait,
    "report": cmd_report,
    "latest-report": cmd_latest_report,
    "reports": cmd_reports,
    "claim": cmd_claim,
    "stage": cmd_stage,
    "state": cmd_state,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    rest = argv[1:]
    if cmd in ("", "-h", "--help"):
        print(HELP)
        return 0
    handler = DISPATCH.get(cmd)
    if handler is None:
        return fail(f"unknown command: {cmd} (try --help)", 2)
    fac = Factory.from_env()
    try:
        return handler(fac, rest)
    except Die as e:
        return fail(str(e), 2)


if __name__ == "__main__":
    raise SystemExit(main())
