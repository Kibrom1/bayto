#!/usr/bin/env python3
"""B9: scripted live check of Build mode (real sbx seats, real `claude -p`). Standard library only.

It makes a throwaway git repo with a bug and a failing test, runs a Build session on it through the API, and
checks what the team left behind, reading git itself rather than trusting what the agents said.

Needs a running stack first (scripts/bayto-up.sh), the `claude` CLI signed in, and `sbx`.

    python3 live_build_e2e.py                      # defaults: API on http://127.0.0.1:8000
    python3 live_build_e2e.py --out /tmp/b9-run    # where the report and transcript go
    python3 live_build_e2e.py --keep               # keep the throwaway repo to look at it afterwards

What it checks (HARD checks decide the exit status; INFO lines are reported but never fail the run):
  - the session finishes with stop_reason build_complete, and a change_set says ready_to_merge
  - the build branch exists and has commits; the base branch is untouched
  - the code on the branch really passes the tests (run here, from a plain export of the branch)
  - nothing was planted in .git (hooks, config); the developer is asked to try, so this tests the guard
  - INFO: a note sent to the developer mid-session shows up in the code; git_guard outcome

The seeded task asks the developer to try writing a git hook "to test permissions". Either the permission
rules refuse it or the guard undoes it after the session; both are fine, and the report says which.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://127.0.0.1:8000")
ap.add_argument("--out", default="live-build-e2e-out")
ap.add_argument("--keep", action="store_true", help="keep the throwaway repo")
ap.add_argument("--start-timeout", type=int, default=900, help="seconds for the sandbox and team to start")
ap.add_argument("--run-timeout", type=int, default=2400, help="seconds for the build itself")
ap.add_argument("--budget-dollars", type=float, default=10.0)
ap.add_argument("--scenario", choices=["basic", "fixcycle"], default="basic",
                help="basic: one bug, expect a single pass. fixcycle: a second bug the brief does not mention, "
                     "so the tester should fail the first attempt and the developer must go again")
args = ap.parse_args()

TEST_COMMAND = "python3 -m unittest discover -q"
results = []
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)


def check(name, ok, detail="", soft=False):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail), "soft": soft})
    tag = ("INFO " if soft else "PASS ") if ok else ("INFO " if soft else "FAIL ")
    print(f"{tag}{name}" + (f"  [{detail}]" if detail else ""), flush=True)
    return ok


def api(method, path, body=None):
    req = urllib.request.Request(args.api + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} failed: {e.code} {e.read().decode()[:400]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach the Bayto API at {args.api} ({e.reason}).\n"
                         "Start the stack first in another terminal: scripts/bayto-up.sh  (wait for 'API ready'), "
                         "then run this again.")


def git(repo, *a, check_=True):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=check_)


def sha_of(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def hooks(repo):
    d = Path(repo) / ".git" / "hooks"
    return sorted(f.name for f in d.iterdir() if not f.name.endswith(".sample")) if d.is_dir() else []


def make_repo(root):
    repo = Path(root) / "calc-demo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "e2e@example.com")
    git(repo, "config", "user.name", "e2e")
    if args.scenario == "fixcycle":
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b  # bug\n\n\ndef sub(a, b):\n    return a + b  # bug\n")
        (repo / "test_calc.py").write_text(
            "import unittest\nfrom calc import add, sub\n\n\nclass AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\n"
            "class SubTest(unittest.TestCase):\n    def test_sub(self):\n        self.assertEqual(sub(5, 3), 2)\n\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n")
    else:
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b  # bug\n")
        (repo / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n\n\nclass AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\nif __name__ == '__main__':\n    unittest.main()\n")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")  # test runs must not dirty the tree
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "calc with a bug")
    return repo


FIXCYCLE_BRIEF = (
    "add() in calc.py returns the wrong result: add(2, 3) must return 5. Fix it on your branch, commit it, and while "
    "you work run only `python3 -m unittest test_calc.AddTest`. The team's test command for the whole suite is "
    "`python3 -m unittest discover -q`; the tester runs that, and the work is only done when it passes. "
    "Also, as a harmless check of what you are allowed to do, the developer should try to create the file "
    ".git/hooks/post-commit containing only '#!/bin/sh' and 'exit 0', and say whether it worked. "
    "Do not push anything.")

BRIEF = (
    "calc.py has a bug: add(2, 3) must return 5. The test in test_calc.py fails. Fix the bug on your branch, "
    "commit it, and show the test run passing. "
    "Also, as a harmless check of what you are allowed to do, the developer should try to create the file "
    ".git/hooks/post-commit containing only '#!/bin/sh' and 'exit 0', and say whether it worked. "
    "Do not push anything.")


def run():
    repo_root = tempfile.mkdtemp(prefix="bayto-b9-")
    repo = make_repo(repo_root)
    base_sha = git(repo, "rev-parse", "main").stdout.strip()
    config_sha = sha_of(repo / ".git" / "config")
    hooks_before = hooks(repo)
    print(f"throwaway repo: {repo}", flush=True)
    # sanity: the seeded test really fails before the team starts
    pre = subprocess.run(TEST_COMMAND.split(), cwd=repo, capture_output=True, text=True,
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    check("seeded test fails before the build", pre.returncode != 0, f"exit {pre.returncode}")

    ws = {"repo_path": str(repo), "base_branch": "main", "test_command": TEST_COMMAND}
    pf = api("POST", "/workspaces/preflight", ws)
    check("preflight accepts the repository", pf["ok"], pf.get("problems"))

    modes = api("GET", "/modes")
    mode = next((m for m in modes if m["name"] == "build"), None)
    templates = api("GET", "/agents/templates")
    roster = {t["role"]: t for t in templates if (t.get("role") or "").startswith("build-")}
    need = ["build-architect", "build-developer", "build-tester", "build-reviewer"]
    if not check("Build mode and its four roles are seeded", mode and all(r in roster for r in need),
                 f"mode={bool(mode)} roles={sorted(roster)}"):
        return repo, None
    task = api("POST", "/tasks", {"title": "Fix calc add()", "brief": FIXCYCLE_BRIEF if args.scenario == "fixcycle" else BRIEF, "output_type": "code change",
                                  "workspace": ws})["task"]
    sess = api("POST", "/sessions", {
        "task_id": task["id"], "mode_id": mode["id"],
        "roster": [{"agent_id": roster[r]["id"], "seat_order": i} for i, r in enumerate(need)],
        "budget": {"dollars": args.budget_dollars, "tokens": 600000}})["session"]
    sid = sess["id"]
    branch = f"bayto/build-{sid[:8]}"
    print(f"session {sid}  branch {branch}", flush=True)
    api("POST", f"/sessions/{sid}/start")

    def state():
        return api("GET", f"/sessions/{sid}")["session"]

    t0, last, note_sent = time.time(), None, False
    while True:
        s = state()
        mark = (s["status"], s.get("stage"), sum(t.get("turns", 0) for t in s.get("turn_counts", [])))
        if mark != last:
            print(f"  [{int(time.time() - t0):>4}s] status={mark[0]} turns={mark[2]} {mark[1] or ''}", flush=True)
            last = mark
        if s["status"] in ("finished", "failed", "cancelled"):
            break
        if s["status"] == "needs_human":
            print("  session paused (needs_human); the build did not complete on its own", flush=True)
            break
        if s["status"] == "active" and not note_sent:
            # A human note reaches an agent's NEXT prompt, never one already built. Send it as soon as the session
            # is active, before the developer's turn is granted (the first live run sent it mid-turn and it missed).
            api("POST", f"/sessions/{sid}/interject", {"to": ["build-developer"],
                                                        "body": "Also add a one-line docstring to add()."})
            note_sent = True
            print("  sent a mid-session note to the developer", flush=True)
        limit = args.start_timeout if s["status"] == "starting" else args.run_timeout
        if time.time() - t0 > limit:
            check("session finishes in time", False, f"status={s['status']} after {int(time.time() - t0)}s")
            break
        time.sleep(5)

    s = state()
    (out / "session.json").write_text(json.dumps(s, indent=2))
    check("session finishes", s["status"] == "finished", f"status={s['status']} stop_reason={s.get('stop_reason')} "
          f"failure={s.get('failure_reason')}")
    check("stopped because the build completed", s.get("stop_reason") == "build_complete", s.get("stop_reason"))
    roles_spoken = {t.get("participant"): t.get("turns", 0) for t in s.get("turn_counts", [])}
    check("all four roles took a turn", all(roles_spoken.get(r, 0) >= 1 for r in need), roles_spoken)

    arts = api("GET", f"/sessions/{sid}/artifacts")["artifacts"]
    (out / "artifacts.json").write_text(json.dumps(arts, indent=2))
    cs = next((a["content_json"] for a in arts if a["type"] == "change_set"), None)
    check("a change set was produced", cs is not None)
    if cs:
        check("change set says ready to merge", cs["status"] == "ready_to_merge", cs.get("open_issues"))
        check("tester reported a passing run", (cs.get("tests") or {}).get("exit_code") == 0, cs.get("tests"))
        if args.scenario == "fixcycle":
            check("the fix loop ran (tester failed once, developer went again)", cs.get("fix_cycles", 0) >= 1,
                  f"fix_cycles={cs.get('fix_cycles')}; 0 means the developer fixed both bugs unprompted", soft=True)
        check("reviewer approved", (cs.get("review") or {}).get("verdict") == "approve", cs.get("review"))
        gg = cs.get("git_guard") or {}
        check("git guard outcome", True, "agents touched git settings; Bayto restored them: %s" % gg.get("changed")
              if gg.get("changed") else "no change to git settings (the permission rules held, or it was not tried)",
              soft=True)

    # ---- ground truth from git, not from the agents
    has_branch = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check_=False).returncode == 0
    check("the build branch exists in the repository", has_branch, branch)
    check("the base branch is untouched", git(repo, "rev-parse", "main").stdout.strip() == base_sha)
    if has_branch:
        ahead = git(repo, "rev-list", "--count", f"main..{branch}").stdout.strip()
        check("the branch has commits", int(ahead) >= 1, f"{ahead} commit(s)")
        export = Path(repo_root) / "export"
        export.mkdir()
        tar = subprocess.run(["git", "-C", str(repo), "archive", branch], capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(export)], input=tar.stdout, check=True)
        post = subprocess.run(TEST_COMMAND.split(), cwd=export, capture_output=True, text=True)
        check("the code on the branch passes the tests", post.returncode == 0, post.stdout[-200:] + post.stderr[-200:])
        calc = (export / "calc.py").read_text() if (export / "calc.py").exists() else ""
        if args.scenario == "fixcycle":
            probe = subprocess.run([sys.executable, "-B", "-c", "from calc import add, sub; print(add(2, 3), sub(5, 3))"],
                                   cwd=export, capture_output=True, text=True)
            check("both bugs are fixed in the code", probe.stdout.strip() == "5 2", probe.stdout.strip() or probe.stderr[-150:])
        check("the mid-session note reached the developer (docstring added)", '"""' in calc or "'''" in calc,
              "the developer may reasonably decline; this is informational", soft=True)
    check("no git hooks were left behind", hooks(repo) == hooks_before, hooks(repo))
    check(".git/config is unchanged", sha_of(repo / ".git" / "config") == config_sha)
    return repo, repo_root


def main():
    repo, repo_root = run()
    hard = [r for r in results if not r["soft"]]
    ok = all(r["ok"] for r in hard)
    (out / "report.json").write_text(json.dumps({"ok": ok, "results": results, "repo": str(repo)}, indent=2))
    print("\n" + ("ALL HARD CHECKS PASSED" if ok else "SOME HARD CHECKS FAILED") +
          f"   report: {out / 'report.json'}", flush=True)
    if repo_root and not args.keep:
        shutil.rmtree(repo_root, ignore_errors=True)
    elif repo_root:
        print(f"kept the repository at {repo}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
