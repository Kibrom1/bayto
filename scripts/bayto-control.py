#!/usr/bin/env python3
"""Bayto control API (backs the Services page of the Bayto UI): start/stop the dev stack and drive sessions/sandboxes from one local page.

Run from anywhere:  python3 scripts/bayto-control.py
It starts the web dev server itself; the controls live in the Bayto UI at http://localhost:5173/services
(this process only serves the API behind it on 127.0.0.1:8787).
Stdlib only. Binds to 127.0.0.1. The API key typed into the page is kept in this process's
memory only (never written to disk, never logged) and is passed to the orchestrator's environment.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import socket
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("BAYTO_CONTROL_PORT", "8787"))
ORCH = "http://127.0.0.1:8000"
PG_NAME = "bayto-pg"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

CONFIG = {
    "streaming": True,
    "summary_model": os.environ.get("BAYTO_SUMMARY_MODEL", "claude-haiku-4-5"),
    "synthesis_model": os.environ.get("BAYTO_SYNTHESIS_MODEL", "claude-sonnet-4-5"),
    "hand_raise_model": os.environ.get("BAYTO_HAND_RAISE_MODEL", "claude-haiku-4-5"),
    "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    # "subscription": moderator calls run through `claude -p` with your login (no key needed); "api": use the key.
    "llm_backend": "api" if os.environ.get("ANTHROPIC_API_KEY") else "subscription",
}


class Proc:
    """A child process in its own process group, with a rolling log."""

    def __init__(self, name, argv, cwd, env_fn=None, pre=None):
        self.name, self.argv, self.cwd, self.env_fn, self.pre = name, argv, cwd, env_fn, pre
        self.p: subprocess.Popen | None = None
        self.log = collections.deque(maxlen=3000)
        self.launching = False

    def running(self):
        return self.launching or (self.p is not None and self.p.poll() is None)

    def start(self):
        if self.running():
            return "already running"
        if shutil.which(self.argv[0]) is None:
            return f"{self.argv[0]} not found on PATH"
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        if self.env_fn:
            env.update(self.env_fn())
        self.launching = True
        threading.Thread(target=self._launch, args=(env,), daemon=True).start()
        return "starting (see the log)"

    def _launch(self, env):
        try:
            self._launch_inner(env)
        finally:
            self.launching = False

    def _launch_inner(self, env):
        # Runs off the request thread: `pre` (for example `uv sync`) can take a while.
        if self.pre:
            self.log.append(f"--- preparing: {' '.join(self.pre)}")
            r = subprocess.run(self.pre, cwd=self.cwd, env=env, capture_output=True, text=True)
            self.log.extend((r.stdout + r.stderr).strip().splitlines()[-15:])
            if r.returncode:
                self.log.append(f"--- preparation failed with code {r.returncode}; not starting")
                return
        self.log.append(f"--- starting {' '.join(self.argv)} in {self.cwd}")
        self.p = subprocess.Popen(self.argv, cwd=self.cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.launching = False
        self._pump(self.p)

    def _pump(self, p):
        for line in p.stdout:
            self.log.append(line.rstrip("\n"))
        self.log.append(f"--- exited with code {p.wait()}")

    def stop(self):
        if not self.running():
            return "not running"
        if self.p is None or self.p.poll() is not None:
            return "still preparing; try again in a moment"
        pgid = os.getpgid(self.p.pid)
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(50):
            if self.p.poll() is not None:
                return "stopped"
            time.sleep(0.1)
        os.killpg(pgid, signal.SIGKILL)
        return "killed"

    def status(self):
        return "running" if self.running() else "stopped"


def run(argv, timeout=60):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, f"{argv[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def orch_env():
    # `src` on the path is a safety net for an editable install that went missing from the venv.
    e = {"PYTHONPATH": str(ROOT / "orchestrator" / "src") + os.pathsep + str(ROOT / "agent-comms" / "src"),
         "BAYTO_SUMMARY_MODEL": CONFIG["summary_model"], "BAYTO_SYNTHESIS_MODEL": CONFIG["synthesis_model"],
         "BAYTO_HAND_RAISE_MODEL": CONFIG["hand_raise_model"]}
    if CONFIG["streaming"]:
        e["BAYTO_STREAMING_TURNS"] = "1"
    if CONFIG["llm_backend"] == "api":
        e["BAYTO_LLM_BACKEND"] = "api"
        if CONFIG["api_key"]:
            e["ANTHROPIC_API_KEY"] = CONFIG["api_key"]
    else:
        e["BAYTO_LLM_BACKEND"] = "cli"
    return e


ORCH_PROC = Proc("orchestrator", ["uv", "run", "--all-extras", "uvicorn", "orchestrator.app:app", "--port", "8000"],
                 str(ROOT / "orchestrator"), orch_env, pre=["uv", "sync", "--all-extras"])
WEB_PROC = Proc("web", ["npm", "run", "dev"], str(ROOT / "web"))


def port_open(port=5432):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def pg_status():
    # Any Postgres on 5432 counts, including one started outside this panel (another container, brew).
    if port_open():
        return "running"
    code, out = run(["docker", "inspect", "-f", "{{.State.Running}}", PG_NAME], 15)
    if code != 0:
        if code == 127:
            return "docker missing"
        if "Cannot connect" in out or "daemon" in out or "docker.sock" in out:
            return "docker not running"
        return "absent" if "No such" in out else "stopped"
    return "running" if out.strip() == "true" else "stopped"


def pg_start():
    st = pg_status()
    if st == "running":
        return "already running"
    if st in ("docker missing", "docker not running"):
        return ("Docker is not installed or on PATH." if st == "docker missing"
                else "Docker is not running. Open Docker Desktop, wait for it to start, then try again.")
    if st == "absent":
        code, out = run(["docker", "run", "-d", "--name", PG_NAME, "-e", "POSTGRES_USER=bayto", "-e",
                         "POSTGRES_PASSWORD=bayto", "-e", "POSTGRES_DB=bayto", "-p", "5432:5432",
                         "postgres:16-alpine"], 180)
    else:
        code, out = run(["docker", "start", PG_NAME], 60)
    return out if code else "started"


def pg_stop():
    code, out = run(["docker", "stop", PG_NAME], 60)
    return out if code else "stopped"


def orch_call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ORCH + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw[:300]}
    except Exception as e:
        return 0, {"error": f"orchestrator unreachable: {e}"}


def state():
    code, tasks = orch_call("GET", "/tasks")
    sessions = []
    if code == 200:
        for t in tasks:
            sid = t.get("last_session_id")
            if sid:
                c, s = orch_call("GET", f"/sessions/{sid}")
                sessions.append({"task": t["title"], "id": sid,
                                 "status": (s.get("session") or s).get("status") if c == 200 else f"error {c}"})
    return {
        "services": {"postgres": pg_status(), "orchestrator": ORCH_PROC.status(), "web": WEB_PROC.status()},
        "config": {**CONFIG, "api_key": "set" if CONFIG["api_key"] else ""},
        "sessions": sessions,
    }


def do_action(a):
    kind = a.get("action")
    if kind == "service":
        name, op = a.get("name"), a.get("op")
        if name == "postgres":
            return pg_start() if op == "start" else pg_stop()
        proc = {"orchestrator": ORCH_PROC, "web": WEB_PROC}.get(name)
        if proc is None or op not in ("start", "stop"):
            return "bad request"
        if name == "web" and op == "stop" and a.get("from_web"):
            return "the web server serves this page; stop it from the terminal running bayto-control.py (Ctrl+C)"
        return proc.start() if op == "start" else proc.stop()
    if kind == "config":
        for k in ("summary_model", "synthesis_model", "hand_raise_model"):
            if isinstance(a.get(k), str) and NAME_RE.match(a[k]):
                CONFIG[k] = a[k]
        if isinstance(a.get("streaming"), bool):
            CONFIG["streaming"] = a["streaming"]
        if a.get("llm_backend") in ("subscription", "api"):
            CONFIG["llm_backend"] = a["llm_backend"]
        if isinstance(a.get("api_key"), str) and a["api_key"]:
            CONFIG["api_key"] = a["api_key"].strip()
        return "saved (restart the orchestrator to apply)"
    if kind == "session":
        sid, op = a.get("id", ""), a.get("op")
        if not UUID_RE.match(sid) or op not in ("start", "pause", "resume", "stop"):
            return "bad request"
        code, body = orch_call("POST", f"/sessions/{sid}/{op}")
        return f"{op}: HTTP {code} {json.dumps(body)[:300]}"
    if kind == "sbx":
        op, name = a.get("op"), a.get("name", "")
        if op == "ls":
            return run(["sbx", "ls"], 30)[1]
        if op in ("stop",) and NAME_RE.match(name):
            return run(["sbx", op, name], 120)[1] or f"sbx {op} {name}: done"  # unverified subcommand
        return "bad request"
    return "bad request"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok_host(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, "bad host", "text/plain")
        if self.path == "/":
            return self._send(200, "Bayto control API. The UI is the Services page: http://localhost:5173/services", "text/plain; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, json.dumps(state()))
        if self.path.startswith("/api/log"):
            name = self.path.split("name=")[-1]
            proc = {"orchestrator": ORCH_PROC, "web": WEB_PROC}.get(name, ORCH_PROC)
            return self._send(200, "\n".join(proc.log), "text/plain; charset=utf-8")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._ok_host() or self.headers.get("x-bayto-control") != "1":
            return self._send(403, "forbidden", "text/plain")
        n = int(self.headers.get("content-length") or 0)
        try:
            a = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, "bad json", "text/plain")
        if self.path == "/api/action":
            return self._send(200, do_action(a), "text/plain; charset=utf-8")
        self._send(404, "not found", "text/plain")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(WEB_PROC.start() and "web dev server: " + WEB_PROC.status())
    print(f"Bayto control on http://127.0.0.1:{PORT}  (Ctrl+C stops this panel and the services it started)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (WEB_PROC, ORCH_PROC):
            p.stop()


if __name__ == "__main__":
    main()
