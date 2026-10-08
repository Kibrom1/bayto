#!/usr/bin/env python3
"""Bayto control panel: start/stop the dev stack and drive sessions/sandboxes from one local page.

Run from anywhere:  python3 scripts/bayto-control.py     then open http://127.0.0.1:8787
Stdlib only. Binds to 127.0.0.1. The API key typed into the page is kept in this process's
memory only (never written to disk, never logged) and is passed to the orchestrator's environment.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
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
}


class Proc:
    """A child process in its own process group, with a rolling log."""

    def __init__(self, name, argv, cwd, env_fn=None):
        self.name, self.argv, self.cwd, self.env_fn = name, argv, cwd, env_fn
        self.p: subprocess.Popen | None = None
        self.log = collections.deque(maxlen=400)

    def running(self):
        return self.p is not None and self.p.poll() is None

    def start(self):
        if self.running():
            return "already running"
        if shutil.which(self.argv[0]) is None:
            return f"{self.argv[0]} not found on PATH"
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        if self.env_fn:
            env.update(self.env_fn())
        self.log.append(f"--- starting {' '.join(self.argv)} in {self.cwd}")
        self.p = subprocess.Popen(self.argv, cwd=self.cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True)
        threading.Thread(target=self._pump, args=(self.p,), daemon=True).start()
        return "started"

    def _pump(self, p):
        for line in p.stdout:
            self.log.append(line.rstrip("\n"))
        self.log.append(f"--- exited with code {p.wait()}")

    def stop(self):
        if not self.running():
            return "not running"
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
    e = {"BAYTO_SUMMARY_MODEL": CONFIG["summary_model"], "BAYTO_SYNTHESIS_MODEL": CONFIG["synthesis_model"],
         "BAYTO_HAND_RAISE_MODEL": CONFIG["hand_raise_model"]}
    if CONFIG["streaming"]:
        e["BAYTO_STREAMING_TURNS"] = "1"
    if CONFIG["api_key"]:
        e["ANTHROPIC_API_KEY"] = CONFIG["api_key"]
    return e


ORCH_PROC = Proc("orchestrator", ["uv", "run", "uvicorn", "orchestrator.app:app", "--port", "8000"],
                 str(ROOT / "orchestrator"), orch_env)
WEB_PROC = Proc("web", ["npm", "run", "dev"], str(ROOT / "web"))


def pg_status():
    code, out = run(["docker", "inspect", "-f", "{{.State.Running}}", PG_NAME], 15)
    if code != 0:
        return "absent" if "No such" in out else ("docker unavailable" if code == 127 else "stopped")
    return "running" if out.strip() == "true" else "stopped"


def pg_start():
    st = pg_status()
    if st == "running":
        return "already running"
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
        return proc.start() if op == "start" else proc.stop()
    if kind == "config":
        for k in ("summary_model", "synthesis_model", "hand_raise_model"):
            if isinstance(a.get(k), str) and NAME_RE.match(a[k]):
                CONFIG[k] = a[k]
        if isinstance(a.get("streaming"), bool):
            CONFIG["streaming"] = a["streaming"]
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


PAGE = r"""<!doctype html><meta charset=utf-8><title>Bayto control</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fff;--fg:#1c1b19;--mut:#6b6a66;--line:#e2e0da;--ok:#1f7a3d;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--mut:#9a9890;--line:#2e2d29;--ok:#5fc47e;--bad:#f2867f}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0;padding:16px;max-width:900px;margin-inline:auto}
h1{font-size:18px}h2{font-size:14px;margin:24px 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
button{font:inherit;padding:3px 10px;border:1px solid var(--line);background:none;color:var(--fg);border-radius:6px;cursor:pointer}
input[type=text],input[type=password]{font:inherit;padding:4px 6px;border:1px solid var(--line);background:none;color:var(--fg);border-radius:6px;width:240px}
.running,.active{color:var(--ok)}.failed,.absent{color:var(--bad)}pre{background:none;border:1px solid var(--line);padding:8px;border-radius:6px;max-height:240px;overflow:auto;white-space:pre-wrap;font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}
</style>
<h1>Bayto control</h1><div id=msg style="color:var(--mut)"></div>
<h2>Services</h2><table id=svc></table>
<h2>Orchestrator settings</h2>
<div class=row><label>Summary model <input type=text id=sm></label><label>Synthesis model <input type=text id=sy></label></div>
<div class=row><label>Anthropic API key <input type=password id=key placeholder="kept in memory only" autocomplete=off></label><span id=keyst></span>
<label><input type=checkbox id=stream> streaming turns</label><button onclick=saveCfg()>Save</button></div>
<div style="color:var(--mut)">Changes apply the next time the orchestrator starts.</div>
<h2>Sessions</h2><table id=ses></table>
<div class=row><input type=text id=sid placeholder="session id"><button onclick="sess(sid.value,'start')">start</button>
<button onclick="sess(sid.value,'pause')">pause</button><button onclick="sess(sid.value,'resume')">resume</button><button onclick="sess(sid.value,'stop')">stop</button></div>
<h2>Sandboxes</h2><div class=row><button onclick="sbx('ls')">list</button><input type=text id=sbn placeholder="sandbox name"><button onclick="sbx('stop')">stop sandbox</button></div>
<pre id=sbo></pre>
<h2>Logs</h2><div class=row><button onclick="lg='orchestrator'">orchestrator</button><button onclick="lg='web'">web</button></div><pre id=log></pre>
<p><a id=weblink href="http://localhost:5173" target=_blank>Open Bayto UI</a></p>
<script>
let lg='orchestrator';
const H={'content-type':'application/json','x-bayto-control':'1'};
async function act(o){const r=await fetch('/api/action',{method:'POST',headers:H,body:JSON.stringify(o)});const t=await r.text();msg.textContent=t;refresh();return t}
function svcBtn(n,s){return `<button onclick="act({action:'service',name:'${n}',op:'start'})">start</button> <button onclick="act({action:'service',name:'${n}',op:'stop'})">stop</button>`}
async function refresh(){
 const s=await (await fetch('/api/state')).json();
 svc.innerHTML=Object.entries(s.services).map(([n,v])=>`<tr><td>${n}</td><td class="${v}">${v}</td><td>${svcBtn(n,v)}</td></tr>`).join('');
 ses.innerHTML='<tr><th>task</th><th>session</th><th>status</th></tr>'+s.sessions.map(x=>`<tr><td>${x.task}</td><td><a href="#" onclick="sid.value='${x.id}';return false">${x.id.slice(0,8)}</a> <a target=_blank href="http://localhost:5173/room/${x.id}">room</a></td><td class="${x.status}">${x.status}</td></tr>`).join('');
 if(document.activeElement!==sm&&!sm.value){sm.value=s.config.summary_model;sy.value=s.config.synthesis_model;stream.checked=s.config.streaming}
 keyst.textContent=s.config.api_key?'key set':'no key';
 log.textContent=(await (await fetch('/api/log?name='+lg)).text())}
function saveCfg(){act({action:'config',summary_model:sm.value,synthesis_model:sy.value,streaming:stream.checked,api_key:key.value});key.value=''}
function sess(id,op){act({action:'session',id:id.trim(),op})}
async function sbx(op){sbo.textContent=await act({action:'sbx',op,name:sbn.value.trim()})}
refresh();setInterval(refresh,3000);
</script>"""


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
            return self._send(200, PAGE, "text/html; charset=utf-8")
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
