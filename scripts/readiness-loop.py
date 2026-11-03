#!/usr/bin/env python3
"""Run Build sessions back to back (the continuous production-readiness review) until a deadline or a STOP file.

Usage: python3 scripts/readiness-loop.py --template SESSION_ID [--hours 24] [--repo PATH] [--stop-file STOP]

SESSION_ID is an earlier Build session of the task: its team, per-seat models and budget are copied into every new
session. Standard library only. Needs the orchestrator running (and, for usage-limit waits to be automatic,
BAYTO_AUTO_RESUME_ON_LIMIT=1). Nothing here touches git except a read-only `git status` of --repo.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NUDGE = ("Continue. Keep the change small, run the tests, and end your turn with a VERDICT line "
         "(for example 'VERDICT: implemented'), with the command you ran and its exit code.")
MAX_NUDGES = 3          # per session; after that the session is ended and the next round starts
PAUSE_GRACE = 15 * 60   # seconds a session may sit paused before a nudge (leaves time for auto-resume after a limit)
MAX_FAILURES = 5        # rounds failing in a row before the loop gives up
FAIL_BACKOFF = 10 * 60


def decide(status: str, paused_for: float, nudges: int) -> str:
    """What to do about a session in `status`: wait | next_round | failed | nudge | end."""
    if status == "finished":
        return "next_round"
    if status in ("failed", "orphaned"):
        return "failed"
    if status == "needs_human":
        if paused_for < PAUSE_GRACE:
            return "wait"
        return "nudge" if nudges < MAX_NUDGES else "end"
    return "wait"


def call(api: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api + path, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"error": str(e)}


def repo_dirty(repo: str | None) -> str:
    if not repo:
        return ""
    out = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else f"git status failed: {out.stderr.strip()[:200]}"


def sync_main(repo: str | None, base: str = "main") -> str:
    """Put --repo on the latest `base` (checkout + fast-forward pull) so a round starts from what is already merged,
    not from an old copy that makes the team redo fixed work. Returns "" on success, else why not. A dirty tree is
    refused before anything changes."""
    if not repo:
        return ""
    for cmd in (["git", "-C", repo, "checkout", base], ["git", "-C", repo, "pull", "--ff-only", "origin", base]):
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            return f"{' '.join(cmd[3:])} failed: {(out.stderr or out.stdout).strip()[:300]}"
    return ""


def new_session(api: str, template: dict) -> str | None:
    s = template["session"]
    roster = [{"agent_id": t["agent_id"], "seat_order": i, **({"model": t["model"]} if t.get("model") else {}),
               "muted": bool(t.get("muted"))} for i, t in enumerate(s["turn_counts"])]
    budget = {}
    if (s.get("budget") or {}).get("max_cost"):
        budget["dollars"] = s["budget"]["max_cost"]
    if (s.get("budget") or {}).get("max_tokens"):
        budget["tokens"] = s["budget"]["max_tokens"]
    body = {"task_id": s["task_id"], "mode_id": s["mode_id"], "roster": roster, **({"budget": budget} if budget else {})}
    code, out = call(api, "POST", "/sessions", body)
    if code != 201:
        print(f"could not create a session: {code} {out}", flush=True)
        return None
    sid = out["session"]["id"]
    code, out = call(api, "POST", f"/sessions/{sid}/start")
    if code != 202:
        print(f"could not start {sid[:8]}: {code} {out}", flush=True)
        return None
    return sid


def run_round(api: str, sid: str, stop_file: Path, deadline: float) -> str:
    paused_since, nudges = None, 0
    while time.time() < deadline and not stop_file.exists():
        time.sleep(30)
        code, out = call(api, "GET", f"/sessions/{sid}")
        if code != 200:
            continue
        s = out["session"]
        status = s["status"]
        paused_since = (paused_since or time.time()) if status == "needs_human" else None
        action = decide(status, time.time() - paused_since if paused_since else 0, nudges)
        if action == "next_round":
            print(f"  round done: {s.get('stop_reason')} cost ${s['usage']['cost']:.2f}", flush=True)
            return "ok"
        if action == "failed":
            print(f"  round failed: {s.get('failure_reason')}", flush=True)
            return "failed"
        if action == "nudge":
            nudges += 1
            call(api, "POST", f"/sessions/{sid}/interject", {"body": NUDGE})
            call(api, "POST", f"/sessions/{sid}/resume")
            paused_since = None
            print(f"  nudged ({nudges}/{MAX_NUDGES})", flush=True)
        elif action == "end":
            call(api, "POST", f"/sessions/{sid}/stop", {"synthesize": False})
            print("  gave up on this round after repeated pauses", flush=True)
            return "ok"
    call(api, "POST", f"/sessions/{sid}/stop", {"synthesize": False})
    return "stopped"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--template", required=True)
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--repo")
    p.add_argument("--stop-file", default="STOP")
    p.add_argument("--base", default="main", help="branch the Build repo is updated to before each round")
    a = p.parse_args()
    code, template = call(a.api, "GET", f"/sessions/{a.template}")
    if code != 200:
        print(f"template session not found: {code} {template}")
        return 2
    deadline, stop_file = time.time() + a.hours * 3600, Path(a.stop_file)
    rounds = failures = 0
    while time.time() < deadline and not stop_file.exists():
        dirty = repo_dirty(a.repo)
        if dirty:
            print(f"the Build repo is not clean, waiting 5 minutes:\n{dirty[:400]}", flush=True)
            time.sleep(300)
            continue
        problem = sync_main(a.repo, a.base)
        if problem:
            print(f"could not update the Build repo to {a.base}, waiting 5 minutes: {problem}", flush=True)
            time.sleep(300)
            continue
        sid = new_session(a.api, template)
        rounds += 1
        print(f"round {rounds}: session {sid[:8] if sid else None}", flush=True)
        result = run_round(a.api, sid, stop_file, deadline) if sid else "failed"
        failures = failures + 1 if result == "failed" else 0
        if failures >= MAX_FAILURES:
            print(f"{failures} rounds failed in a row, stopping", flush=True)
            return 1
        time.sleep(FAIL_BACKOFF if result == "failed" else 10)
    print(f"finished after {rounds} rounds ({'STOP file' if stop_file.exists() else 'deadline'})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
