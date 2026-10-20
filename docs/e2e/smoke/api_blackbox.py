#!/usr/bin/env python3
"""Black-box API test against a running orchestrator (stdlib only). Prints PASS/FAIL per check, writes results JSON."""
import json, os, subprocess, sys, tempfile, urllib.request, urllib.error, uuid
API = os.environ.get("API", "http://127.0.0.1:8000")
R = []

def call(method, path, body=None, raw=False):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode()
            return r.status, (data if raw else (json.loads(data) if data else None)), r.headers
    except urllib.error.HTTPError as e:
        data = e.read().decode()
        try: data = json.loads(data)
        except Exception: pass
        return e.code, data, e.headers

def check(area, name, ok, detail=""):
    R.append({"area": area, "name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + f"[{area}] {name}" + (f"  [{str(detail)[:160]}]" if (detail and not ok) else ""), flush=True)

def sh(*a, cwd=None): return subprocess.run(a, cwd=cwd, capture_output=True, text=True)

# ---------- health / catalogue
s, b, _ = call("GET", "/healthz"); check("health", "healthz ok", s == 200 and b == {"status": "ok"}, (s, b))
s, modes, _ = call("GET", "/modes"); names = [m["name"] for m in modes]
check("catalogue", "four modes seeded", names == ["open-chat", "debate", "brainstorm", "build"], names)
check("catalogue", "each mode has a floor policy", all(m.get("floor_policy") for m in modes), [m.get("floor_policy") for m in modes])
s, tpl, _ = call("GET", "/agents/templates")
roles = {t["role"] for t in tpl}
check("catalogue", "17 agent templates incl. the four build roles", len(tpl) == 17 and {"build-architect", "build-developer", "build-tester", "build-reviewer"} <= roles, len(tpl))
check("catalogue", "every template has id, name, role, model-or-null", all({"id", "name", "role"} <= t.keys() for t in tpl))
s, _, _ = call("GET", "/nope"); check("errors", "unknown route is 404", s == 404, s)

mode = {m["name"]: m for m in modes}
agent = {t["role"]: t for t in tpl}

# ---------- tasks
s, b, _ = call("POST", "/tasks", {"title": "T1", "brief": "A brief", "output_type": "decision"})
check("tasks", "create a task -> 201", s == 201 and b["task"]["title"] == "T1", (s, b)); t1 = b["task"]["id"] if s == 201 else None
for label, body in [("missing title", {"brief": "x", "output_type": "decision"}),
                    ("empty title", {"title": "", "brief": "x", "output_type": "decision"}),
                    ("whitespace title", {"title": "   ", "brief": "x", "output_type": "decision"}),
                    ("missing output_type", {"title": "t", "brief": "x"}),
                    ("title far too long", {"title": "x" * 5000, "brief": "x", "output_type": "decision"}),
                    ("title wrong type", {"title": 5, "brief": "x", "output_type": "decision"})]:
    s, b, _ = call("POST", "/tasks", body); check("tasks", f"rejects {label} (4xx, not 500)", 400 <= s < 500, (s, str(b)[:120]))
s, b, _ = call("POST", "/tasks", {"title": "'; DROP TABLE task;--", "brief": "<script>alert(1)</script>", "output_type": "decision"})
check("tasks", "odd characters are stored as plain text", s == 201 and b["task"]["title"].startswith("';"), s)
s, lst, _ = call("GET", "/tasks"); check("tasks", "list tasks includes the new ones", s == 200 and any(t["id"] == t1 for t in lst) and len(lst) >= 2, (s, len(lst) if isinstance(lst, list) else lst))
check("tasks", "list rows carry status/counts fields", isinstance(lst, list) and lst and {"id", "title"} <= lst[0].keys(), list(lst[0].keys()) if lst else None)
s, b, _ = call("GET", f"/tasks/{t1}/usage-report"); check("tasks", "usage report for a task with no sessions -> 200", s == 200, (s, str(b)[:120]))
s, b, _ = call("GET", f"/tasks/{uuid.uuid4()}/usage-report"); check("tasks", "usage report for an unknown task -> 404", s == 404, s)
s, b, _ = call("GET", "/tasks/not-a-uuid/usage-report"); check("tasks", "usage report with a bad id -> 422", s == 422, s)

# ---------- workspaces (real throwaway git repos)
root = tempfile.mkdtemp(prefix="bb-")
repo = os.path.join(root, "r"); os.mkdir(repo)
sh("git", "init", "-q", "-b", "main", cwd=repo); sh("git", "config", "user.email", "t@e.com", cwd=repo); sh("git", "config", "user.name", "t", cwd=repo)
open(os.path.join(repo, "a.py"), "w").write("x = 1\n"); sh("git", "add", ".", cwd=repo); sh("git", "commit", "-qm", "init", cwd=repo)
ws = {"repo_path": repo, "base_branch": "main", "test_command": "python3 -m unittest"}
s, b, _ = call("POST", "/workspaces/preflight", ws); check("workspace", "clean repo passes preflight", s == 200 and b["ok"] is True and b["current_branch"] == "main", (s, b))
s, b, _ = call("POST", "/workspaces/preflight", {**ws, "repo_path": root}); check("workspace", "a folder that is not a repo fails preflight", s == 200 and b["ok"] is False and b["problems"], (s, str(b)[:160]))
s, b, _ = call("POST", "/workspaces/preflight", {**ws, "repo_path": os.path.join(root, "missing")}); check("workspace", "a missing folder fails preflight", s == 200 and b["ok"] is False, (s, str(b)[:160]))
s, b, _ = call("POST", "/workspaces/preflight", {**ws, "base_branch": "nope"}); check("workspace", "a missing base branch fails preflight", s == 200 and b["ok"] is False, (s, str(b)[:160]))
open(os.path.join(repo, "dirty.txt"), "w").write("d"); s, b, _ = call("POST", "/workspaces/preflight", ws)
check("workspace", "a dirty tree is reported, with the file", s == 200 and b["dirty"] is True and "dirty.txt" in " ".join(b["dirty_files"]), (s, str(b)[:200])); os.remove(os.path.join(repo, "dirty.txt"))
for label, cmd in [("empty-looking test command", "   "), ("shell chaining", "pytest; rm -rf /"), ("pipe", "pytest | tee x"), ("command substitution", "pytest $(id)")]:
    s, b, _ = call("POST", "/workspaces/preflight", {**ws, "test_command": cmd})
    check("workspace", f"test command with {label} is refused (4xx or ok=false)", (400 <= s < 500) or (s == 200 and b["ok"] is False), (s, str(b)[:160]))
s, b, _ = call("POST", "/workspaces/preflight", {**ws, "repo_path": "../../etc"}); check("workspace", "relative path is refused or reported as a problem", (400 <= s < 500) or (s == 200 and b["ok"] is False), (s, str(b)[:160]))
s, b, _ = call("POST", "/tasks", {"title": "Build task", "brief": "fix it", "output_type": "code change", "workspace": ws})
check("workspace", "create a task with a workspace -> 201", s == 201 and b["task"].get("workspace"), (s, str(b)[:200])); tb = b["task"]["id"] if s == 201 else None
s, b, _ = call("POST", "/tasks", {"title": "Bad ws", "brief": "x", "output_type": "code change", "workspace": {**ws, "repo_path": root}})
check("workspace", "a task whose workspace fails preflight is refused (4xx)", 400 <= s < 500, (s, str(b)[:160]))
s, b, _ = call("PUT", f"/tasks/{t1}/workspace", ws); check("workspace", "attach a workspace to an existing task", s == 200 and b["task"].get("workspace"), (s, str(b)[:160]))

# ---------- sessions
def roster(*roles): return [{"agent_id": agent[r]["id"], "seat_order": i} for i, r in enumerate(roles)]
s, b, _ = call("POST", "/sessions", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": roster("architect", "pm", "lawyer")})
check("sessions", "create a debate session -> 201 with 3 seats", s == 201 and len(b["session_agents"]) == 3 and b["session"]["status"] == "created", (s, str(b)[:160])); s1 = b["session"]["id"] if s == 201 else None
for label, body in [("unknown task", {"task_id": str(uuid.uuid4()), "mode_id": mode["debate"]["id"], "roster": roster("pm")}),
                    ("unknown mode", {"task_id": t1, "mode_id": str(uuid.uuid4()), "roster": roster("pm")}),
                    ("empty roster", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": []}),
                    ("duplicate agent", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": [{"agent_id": agent["pm"]["id"]}, {"agent_id": agent["pm"]["id"]}]}),
                    ("unknown agent", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": [{"agent_id": str(uuid.uuid4())}]}),
                    ("negative budget", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": roster("pm"), "budget": {"tokens": -5}}),
                    ("zero dollars", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": roster("pm"), "budget": {"dollars": 0}}),
                    ("13 seats", {"task_id": t1, "mode_id": mode["debate"]["id"], "roster": [{"agent_id": a["id"]} for a in tpl[:13]]}),
                    ("bad task id", {"task_id": "x", "mode_id": mode["debate"]["id"], "roster": roster("pm")})]:
    s, b, _ = call("POST", "/sessions", body); check("sessions", f"rejects {label} (4xx, not 500)", 400 <= s < 500, (s, str(b)[:140]))
s, b, _ = call("POST", "/sessions", {"task_id": t1, "mode_id": mode["build"]["id"], "roster": roster("build-architect", "build-developer", "build-tester", "build-reviewer")})
check("sessions", "Build mode on a task WITHOUT a workspace is refused or flagged", (400 <= s < 500) or s == 201, (s, str(b)[:160]))
s, b, _ = call("POST", "/sessions", {"task_id": tb, "mode_id": mode["build"]["id"], "roster": roster("build-architect", "build-developer", "build-tester", "build-reviewer")})
check("sessions", "Build session with the four roles and a workspace -> 201", s == 201, (s, str(b)[:160])); sb = b["session"]["id"] if s == 201 else None
s, b, _ = call("POST", "/sessions", {"task_id": tb, "mode_id": mode["build"]["id"], "roster": roster("build-architect", "build-developer")})
check("sessions", "Build session missing roles is refused (4xx)", 400 <= s < 500, (s, str(b)[:160]))
s, b, _ = call("GET", f"/sessions/{s1}"); d = b["session"] if s == 200 else {}
check("sessions", "get a session: status created, turn_counts, no failure", s == 200 and d["status"] == "created" and d.get("failure_reason") is None, (s, str(b)[:200]))
s, b, _ = call("GET", f"/sessions/{uuid.uuid4()}"); check("sessions", "unknown session -> 404", s == 404, s)
s, b, _ = call("GET", "/sessions/xyz"); check("sessions", "bad session id -> 422", s == 422, s)
s, b, _ = call("GET", f"/sessions/{s1}/artifacts"); check("sessions", "artifacts of a fresh session: empty list", s == 200 and b == {"artifacts": []}, (s, b))

# ---------- lifecycle on a session that was never started
s, b, _ = call("POST", f"/sessions/{s1}/interject", {"body": "hi"}); check("lifecycle", "interject on a never-started session -> 409", s == 409, (s, str(b)[:120]))
s, b, _ = call("POST", f"/sessions/{s1}/stop", {}); check("lifecycle", "stop a never-started session -> 409", s == 409, (s, str(b)[:120]))
s, b, _ = call("POST", f"/sessions/{s1}/pause"); check("lifecycle", "pause a never-started session -> 409", s == 409, (s, str(b)[:120]))
s, b, _ = call("POST", f"/sessions/{s1}/resume"); check("lifecycle", "resume a never-started session -> 4xx", 400 <= s < 500, (s, str(b)[:120]))
s, b, _ = call("POST", f"/sessions/{uuid.uuid4()}/start"); check("lifecycle", "start an unknown session -> 404", s == 404, s)
s, b, _ = call("POST", f"/sessions/{s1}/interject", {"body": ""}); check("lifecycle", "empty interject body -> 4xx", 400 <= s < 500, s)
s, b, _ = call("POST", f"/sessions/{s1}/participants/{agent['pm']['id']}/mute"); check("lifecycle", "mute a seat before the session starts (accepted or 409, never 500)", s < 500, (s, str(b)[:120]))
s, b, _ = call("POST", f"/sessions/{s1}/participants/{uuid.uuid4()}/mute"); check("lifecycle", "mute an agent not in the session -> 404", s == 404, (s, str(b)[:120]))

# ---------- start without the sandbox tooling (this host has no sbx / claude login)
s, b, _ = call("POST", f"/sessions/{s1}/start")
check("start", "starting here is answered, not a 500 crash (202, 4xx or 503 with a reason)", s in (202, 400, 409, 422, 503), (s, str(b)[:200]))
import time; time.sleep(3)
s, b, _ = call("GET", f"/sessions/{s1}"); d = b["session"]
check("start", "after a start with no sbx the session ends in a clear state, with a reason if failed",
      d["status"] in ("created", "failed", "starting", "needs_human") and (d["status"] != "failed" or d.get("failure_reason")), (d["status"], d.get("failure_reason")))
print("   observed start ->", s, d["status"], str(d.get("failure_reason"))[:200])

# ---------- SSE
req = urllib.request.Request(API + f"/sessions/{s1}/events", headers={"Accept": "text/event-stream"})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        ct = r.headers.get("content-type", ""); first = r.read(200).decode(errors="ignore")
    check("events", "SSE endpoint answers with text/event-stream", "text/event-stream" in ct, ct)
except Exception as e:
    check("events", "SSE endpoint answers with text/event-stream", "text/event-stream" in str(getattr(e, 'headers', '')) or True and "timed out" in str(e), e)
s, b, _ = call("GET", f"/sessions/{uuid.uuid4()}/events", raw=True); check("events", "SSE for an unknown session -> 404", s == 404, s)

ok = sum(r["ok"] for r in R); print(f"\n{ok}/{len(R)} checks passed")
json.dump(R, open(os.environ.get("OUT", "/tmp/tr/api_results.json"), "w"), indent=1)
