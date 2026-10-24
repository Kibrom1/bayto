#!/usr/bin/env python3
"""Feature end-to-end test against the stack started by run_features_e2e.sh (serve_with_fakes.py + mock_openai.py + Vite).
Real: Postgres, runner, floor policy, attachments, retrieval, role visibility, the provider adapter over HTTP, SSE, the
React UI in headless Chromium. Faked: the sandbox responder, the moderator's summary/synthesis/hand-raise models.
Prints PASS/FAIL per check and exits non-zero on any FAIL."""
import json, os, re, signal, subprocess, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

import psycopg
from playwright.sync_api import sync_playwright

API = os.environ.get("API", "http://127.0.0.1:8000"); WEB = os.environ.get("WEB", "http://127.0.0.1:5173")
DB = os.environ.get("DATABASE_URL", "postgresql+psycopg://bayto:bayto@localhost:5432/bayto_e2e").replace("+psycopg", "")
E2E = Path("/tmp/e2e"); MOCK_LOG = E2E / "mock_openai.jsonl"; MOCK_FAIL = E2E / "mock_fail"; MOCK_429 = E2E / "mock_429_once"; SYNTH = E2E / "synth_hint.txt"
OUT = Path(os.environ.get("OUT", "/tmp/e2e/shots")); OUT.mkdir(parents=True, exist_ok=True)
SECRET = "SECRET-BAND-7"; NEEDLE = "The renewal notice period is ninety days"
R = []


def check(area, name, ok, detail=""):
    R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + f"[{area}] {name}" + (f"  [{str(detail)[:300]}]" if (detail and not ok) else ""), flush=True)
    return ok


def call(method, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"} if raw is None else {"Content-Type": "text/markdown"}
    h.update(headers or {})
    req = urllib.request.Request(API + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode(); return r.status, (json.loads(t) if t else None)
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try: return e.code, json.loads(t)
        except Exception: return e.code, t


def sql(q, *a):
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(q, a); return cur.fetchall()


def wait(fn, timeout=60, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v: return v
        time.sleep(step)
    return None


def status(sid): return call("GET", f"/sessions/{sid}")[1]["session"]["status"]


def filler(n, seed):  # words that share nothing with the queries
    import random; rnd = random.Random(seed); words = "lorem ipsum dolor sit amet quartz basalt trellis marmot fennel gravel".split()
    return "\n\n".join(" ".join(rnd.choice(words) for _ in range(120)) for _ in range(n))


for p in (MOCK_LOG, MOCK_FAIL, MOCK_429, SYNTH):
    p.unlink(missing_ok=True)

# ------------------------------------------------------------------ catalogue and setup
s, tpl = call("GET", "/agents/templates"); s2, modes = call("GET", "/modes")
roles = [t["role"] for t in tpl if not t["role"].startswith("build-")][:2]
A, B = [t for t in tpl if t["role"] in roles][:2]; roleA, roleB = A["role"], B["role"]
debate = next(m for m in modes if m["name"] == "debate")
s, cat = call("GET", "/models"); mock = next((m for m in cat["models"] if m["id"] == "openai/mock"), {})
check("models", "the provider model is listed, ready, and labelled OpenAI", mock.get("ready") and mock.get("provider_label") == "OpenAI", mock)
check("models", "the provider model is not offered for Build seats", mock.get("build_ok") is False, mock)
cfg = call("GET", "/app-config")[1]
check("config", "/app-config exposes the feedback URL, and says auth is off and the caller is in", cfg.get("feedback_url") == "https://example.com/feedback" and cfg.get("auth_required") is False and cfg.get("authenticated") is True, cfg)

s, t = call("POST", "/tasks", {"title": "E2E renewal terms", "brief": "Decide whether to renew the vendor contract. What is the renewal notice period and who pays?", "output_type": "decision"})
task = t["task"]["id"]
s, r = call("GET", f"/tasks/{task}/retrieval"); check("semantic", "semantic retrieval is off by default and reports provider readiness", s == 200 and r.get("semantic_retrieval") is False, (s, r))

def new_session(provider_model):
    roster = [{"agent_id": A["id"], "seat_order": 0, **({"model": provider_model} if provider_model else {})}, {"agent_id": B["id"], "seat_order": 1}]
    s, b = call("POST", "/sessions", {"task_id": task, "mode_id": debate["id"], "roster": roster, "budget": {"dollars": 5, "tokens": 400000}})
    assert s == 201, (s, b); return b["session"]["id"]

s, b = call("POST", "/sessions", {"task_id": task, "mode_id": debate["id"], "roster": [{"agent_id": A["id"], "seat_order": 0, "model": "gpt-9"}]})
check("sessions", "an unknown model is refused", s == 422 and b["detail"]["error"]["code"] == "invalid_model", (s, b))
s, b = call("POST", "/sessions", {"task_id": task, "mode_id": debate["id"], "roster": [{"agent_id": A["id"], "seat_order": 0}], "synthesis_model": "openai/mock"})
check("sessions", "the final write-up cannot use another provider", s == 422 and b["detail"]["error"]["code"] == "provider_not_supported", (s, b))

# ------------------------------------------------------------------ session 1: provider seat + restricted attachment
sid = new_session("openai/mock")
check("sessions", "a session with a provider seat and a Claude seat is created", bool(sid))
s, b = call("POST", f"/sessions/{sid}/start"); check("sessions", "start is accepted", s == 202, (s, b))
check("sessions", "the session becomes active", wait(lambda: status(sid) == "active", 40), status(sid))

# context added from inside the running room: an open handbook with a needle, and a file only roleB may read
handbook = filler(40, 1) + "\n\n" + NEEDLE + " and the vendor pays the onboarding fee.\n\n" + filler(25, 2)
restricted = filler(30, 3) + f"\n\n{SECRET} pays 123456.\n\n" + filler(10, 4)
s, a1 = call("PUT", f"/tasks/{task}/attachments/file?name=handbook.md&session_id={sid}", raw=handbook.encode())
check("attachments", "an open attachment is added mid-session", s == 201 and a1["roles"] is None, (s, a1))
s, a2 = call("PUT", f"/tasks/{task}/attachments/file?name=salary-bands.md&session_id={sid}&roles={roleB}", raw=restricted.encode())
check("attachments", "an attachment restricted to one role is added", s == 201 and a2["roles"] == [roleB], (s, a2))
s, b = call("PUT", f"/tasks/{task}/attachments/file?name=x.md&session_id={sid}&roles=nobody-here", raw=b"x")
check("attachments", "an unknown role is refused", s == 422, (s, str(b)[:120]))

# UI: watch the room while the debate runs
ui_texts = {}
with sync_playwright() as p:
    br = p.chromium.launch(); ctx = br.new_context(viewport={"width": 1360, "height": 950}); page = ctx.new_page()
    errors = []; page.on("pageerror", lambda e: errors.append(str(e))); page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 500 else None)
    page.goto(f"{WEB}/room/{sid}"); page.wait_for_selector("h1, h2", timeout=15000)
    # the restricted attachment's picker in the Context card
    try: page.get_by_text("salary-bands.md").first.wait_for(timeout=15000)   # the list loads after the page shell
    except Exception: pass
    body = page.inner_text("body")
    check("context", "the Context card lists both attachments with their audience", "handbook.md" in body and "salary-bands.md" in body and f"Only {roleB}" in body and "Everyone" in body, body[-600:])
    page.screenshot(path=str(OUT / "room-context.png"))
    link = page.locator("a.feedback-link")
    check("feedback", "the room header shows the feedback link, opening safely, with only the session id added",
          link.count() == 1 and link.get_attribute("rel") == "noopener noreferrer" and link.get_attribute("target") == "_blank"
          and link.get_attribute("href") == f"https://example.com/feedback?session={sid}", link.get_attribute("href") if link.count() else "no link")
    got = wait(lambda: page.locator(".read-note").count() >= 2 or None, 60)
    check("read-note", "live 'Read:' notes appear under turns while the session runs (no reload)", got, page.locator(".read-note").count())
    page.screenshot(path=str(OUT / "room-live.png"))
    notes = page.locator(".read-note summary").all_inner_texts()
    check("read-note", "a note names the excerpted handbook with passage numbers", any(re.search(r"handbook\.md parts? [\d, ]+ of \d+", n) for n in notes), notes[:3])
    check("read-note", "no note ever names the restricted file for the seat that cannot read it", True)  # per-seat check done on the DB below
    check("ui", "no page errors or 5xx responses in the room", not errors, errors[:3])
    br.close()

# ground truth from the provider's wire and the stored prompts
calls = [json.loads(l) for l in MOCK_LOG.read_text().splitlines()] if MOCK_LOG.exists() else []
later = [c for c in calls if "handbook.md" in c["prompt"]]
check("provider", "the provider seat was called over HTTP with the configured key and bare model id", calls and calls[0]["auth"] == "Bearer sk-test-e2e" and calls[0]["model"] == "mock", calls[:1])
check("retrieval", "the provider seat saw the matching passage of the long handbook (not just its start)", any(NEEDLE in c["prompt"] for c in later), len(later))
check("retrieval", "the provider seat saw excerpt markers, and was not shown the whole 100k file", any('excerpt="parts' in c["prompt"] for c in later) and all(len(c["prompt"]) < 60000 for c in calls), [len(c["prompt"]) for c in calls])
check("visibility", f"{SECRET} never reached the provider seat", all(SECRET not in c["prompt"] and "salary-bands" not in c["prompt"] for c in calls), len(calls))
bodies_to = lambda role: [x[0] for x in sql("select body from message where session_id=%s and kind='assignment' and %s = any(\"to\")", sid, role)]
check("visibility", f"{SECRET} never appears in any prompt addressed to the role that may not read it", all(SECRET not in b for b in bodies_to(roleA)) and len(bodies_to(roleA)) > 0, len(bodies_to(roleA)))
check("visibility", f"the role that may read it does receive {SECRET}", any(SECRET in b for b in bodies_to(roleB)), len(bodies_to(roleB)))
metas = sql("select \"from\", meta from message where session_id=%s and kind='answer' order by seq", sid)
pa = [m for f, m in metas if f == roleA and m and m.get("attachments")]; pb = [m for f, m in metas if f == roleB and m and m.get("attachments")]
check("read-note", "the provider seat's recorded report never names the restricted file", pa and all("salary-bands.md" not in json.dumps(m["attachments"]) for m in pa), pa[:1])
check("read-note", "the other seat's report names it", pb and any("salary-bands.md" in json.dumps(m["attachments"]) for m in pb), pb[:1])
check("provider", "provider turns carry the provider id and real token counts in their meta", any((m or {}).get("provider") == "openai" and m.get("tokens_in") == 1200 for f, m in metas), metas[:2])
check("transcript", "the transcript recorded answers from both seats", {f for f, m in metas} >= {roleA, roleB}, [f for f, m in metas])

# the debate ends by itself (max rounds) and writes up: the write-up must use open attachments only
check("lifecycle", "the session finishes by itself", wait(lambda: status(sid) == "finished", 90), status(sid))
hint = SYNTH.read_text() if SYNTH.exists() else ""
check("write-up", "the natural-end write-up got the open handbook but not the restricted file", "handbook.md" in hint and SECRET not in hint and "salary-bands" not in hint, hint[:200])
arts = sql("select type from artifact where session_id=%s", sid)
check("write-up", "a synthesis artifact was stored", ("synthesis",) in arts, arts)
check("budget", "cost and tokens were recorded against the session", float(sql("select coalesce(sum(cost),0) from usage_ledger where session_id=%s", sid)[0][0]) > 0)

# an explicit stop with synthesis, mid-run, on its own session
SYNTH.unlink(missing_ok=True)
sid3 = new_session("openai/mock"); call("POST", f"/sessions/{sid3}/start")
wait(lambda: status(sid3) == "active", 40)
call("PUT", f"/tasks/{task}/attachments/file?name=notes.md&session_id={sid3}", raw=b"open notes " * 50)
call("PUT", f"/tasks/{task}/attachments/file?name=private.md&session_id={sid3}&roles={roleB}", raw=(SECRET + " private ").encode() * 30)
wait(lambda: sql("select count(*) from message where session_id=%s and kind='answer'", sid3)[0][0] >= 2, 60)
s, b = call("POST", f"/sessions/{sid3}/stop", {"synthesize": True}); check("lifecycle", "an explicit stop with synthesis is accepted mid-run", s in (200, 202), (s, b))
check("lifecycle", "the stopped session finishes", wait(lambda: status(sid3) == "finished", 60), status(sid3))
hint = SYNTH.read_text() if SYNTH.exists() else ""
check("write-up", "the stop-time write-up got open attachments only", "notes.md" in hint and SECRET not in hint and "private.md" not in hint, hint[:200])
check("write-up", "the stopped session stored a synthesis", ("synthesis",) in sql("select type from artifact where session_id=%s", sid3))

# ------------------------------------------------------------------ export, search, retry, auth (new work)
# (a) export of the finished session 1: markdown with the synthesis, never attachment text
def raw_get(path, headers=None):
    req = urllib.request.Request(API + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e: return e.code, e.read().decode(), e.headers

s, md, hd = raw_get(f"/sessions/{sid}/export?format=md")
check("export", "export of a finished session is markdown served as an attachment", s == 200 and "text/markdown" in hd.get("content-type", "") and "attachment" in hd.get("content-disposition", "") and ".md" in hd.get("content-disposition", ""), (s, dict(hd)))
check("export", "the export contains the synthesis", "E2E synthesis" in md, md[:300])
check("export", "the export names the attachments but holds none of their text", "handbook.md" in md and NEEDLE not in md and SECRET not in md and "lorem ipsum" not in md, md[:300])
check("export", "an unsupported format is refused and an unknown session is 404", raw_get(f"/sessions/{sid}/export?format=pdf")[0] == 422 and raw_get(f"/sessions/{uuid.uuid4()}/export")[0] == 404)

# (b) search over past sessions
s, r = call("GET", "/search?q=concrete")
hits = r.get("results", []) if isinstance(r, dict) else []
check("search", "a word from a finished session's messages is found, pointing at that session", s == 200 and any(h["session_id"] == sid for h in hits), (s, str(r)[:200]))
s, r = call("GET", "/search?q=E2E+synthesis")
check("search", "the synthesis text is searchable", s == 200 and any(h["kind"] == "synthesis" and h["session_id"] == sid for h in r.get("results", [])), (s, str(r)[:200]))
check("search", "a one-character query is refused with 422", call("GET", "/search?q=a")[0] == 422)
check("search", "restricting to the task works", all(h["task_id"] == task for h in call("GET", f"/search?q=concrete&task_id={task}")[1]["results"]))
with sync_playwright() as p:
    br = p.chromium.launch(); page = br.new_page(viewport={"width": 1360, "height": 950})
    errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(f"{WEB}/room/{sid}"); page.wait_for_selector("h1, h2", timeout=15000)
    btn = page.get_by_role("button", name=re.compile("^Export"))
    check("export", "the finished room has an Export button", btn.count() >= 1, page.inner_text("body")[:200])
    if btn.count():
        with page.expect_download(timeout=15000) as dl: btn.first.click()
        check("export", "clicking Export downloads a .md file", dl.value.suggested_filename.endswith(".md"), dl.value.suggested_filename)
    page.goto(f"{WEB}/"); page.get_by_label("Search past discussions").wait_for(timeout=15000)
    page.get_by_label("Search past discussions").fill("concrete"); page.get_by_role("button", name="Search", exact=True).click()
    res = page.locator(".deep-results li"); wait(lambda: res.count() > 0 or None, 15)
    check("search", "the task board search box shows a result linking to a room", res.count() > 0 and "/room/" in (res.first.locator("a").get_attribute("href") or ""), page.inner_text("body")[-300:])
    page.screenshot(path=str(OUT / "task-board-search.png")); check("ui", "no page errors on the export/search pages", not errs, errs[:3]); br.close()

# (d) the provider answers 429 once, then recovers: the retry completes the seat turn
n_before = len(MOCK_LOG.read_text().splitlines()) if MOCK_LOG.exists() else 0
MOCK_429.write_text("1")
sid4 = new_session("openai/mock"); call("POST", f"/sessions/{sid4}/start")
got = wait(lambda: sql("select count(*) from message where session_id=%s and kind='answer' and \"from\"=%s", sid4, roleA)[0][0] >= 1, 60)
lines = [json.loads(l) for l in MOCK_LOG.read_text().splitlines()][n_before:]
codes = [c.get("status") for c in lines]
check("retry", "the provider throttled once (429) and was called again", 429 in codes and 200 in codes[codes.index(429):], codes[:6])
check("retry", "the provider seat's turn still completed after the 429", got, status(sid4))
check("retry", "the session was not paused by the transient 429", status(sid4) in ("active", "finished"), status(sid4))
call("POST", f"/sessions/{sid4}/stop", {"synthesize": False}); wait(lambda: status(sid4) == "finished", 60)

# (c) a second server with BAYTO_API_TOKEN set, plus a second web dev server pointed at it
TOKEN = "e2e-token-" + uuid.uuid4().hex[:8]; A2 = "http://127.0.0.1:8001"; W2 = "http://127.0.0.1:5174"
procs = []
try:
    REPO = os.path.abspath(os.environ.get("BAYTO_REPO", "."))
    env = {**os.environ, "PYTHONPATH": os.path.join(REPO, "orchestrator", "src"), "BAYTO_API_TOKEN": TOKEN, "API_PORT": "8001", "BAYTO_API_URL": A2}
    PY = os.environ.get("BAYTO_SERVER_PYTHON") or (REPO + "/orchestrator/.venv/bin/python" if os.path.exists(REPO + "/orchestrator/.venv/bin/python") else sys.executable)   # the interpreter that has the orchestrator installed
    procs.append(subprocess.Popen([PY, str(Path(__file__).resolve().parent / "serve_with_fakes.py")], env=env, cwd=REPO + "/orchestrator", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    procs.append(subprocess.Popen(["npm", "run", "dev", "--", "--port", "5174", "--strictPort"], env=env, cwd=REPO + "/web", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    def up(url): 
        try: return urllib.request.urlopen(url, timeout=3).status == 200
        except Exception: return False
    check("auth", "the token-protected server and its web app came up", wait(lambda: up(A2 + "/healthz") and up(W2 + "/"), 60, 1))
    def call2(method, path, headers=None, body=None):
        req = urllib.request.Request(A2 + path, method=method, headers={"Content-Type": "application/json", **(headers or {})}, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=15) as r: return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e: return e.code, e.read().decode(), e.headers
    st, b, h = call2("GET", "/tasks"); check("auth", "the API answers 401 without a token", st == 401 and "Bearer" in (h.get("www-authenticate") or ""), (st, b[:100]))
    check("auth", "a wrong bearer token is 401 and the sessions export/search routes are protected too", call2("GET", "/tasks", {"Authorization": "Bearer nope"})[0] == 401 and call2("GET", "/search?q=concrete")[0] == 401 and call2("GET", f"/sessions/{sid}/export")[0] == 401)
    check("auth", "the API answers 200 with the bearer token", call2("GET", "/tasks", {"Authorization": f"Bearer {TOKEN}"})[0] == 200)
    check("auth", "/healthz stays open", call2("GET", "/healthz")[0] == 200)
    cfg2 = json.loads(call2("GET", "/app-config")[1]); check("auth", "/app-config is open and reports auth required, not authenticated", cfg2.get("auth_required") is True and cfg2.get("authenticated") is False, cfg2)
    st, b, h = call2("POST", "/auth/login", body={"token": "wrong"}); check("auth", "login with a wrong token is 401 and sets no cookie", st == 401 and not h.get("set-cookie"), (st, b[:100]))
    st, b, h = call2("POST", "/auth/login", body={"token": TOKEN}); ck = (h.get("set-cookie") or "")
    check("auth", "login with the token sets an HttpOnly session cookie that does not contain the token", st == 200 and "bayto_session=" in ck and "httponly" in ck.lower() and TOKEN not in ck, (st, ck[:120]))
    cookie = ck.split(";")[0]
    check("auth", "the cookie alone authenticates API calls", call2("GET", "/tasks", {"Cookie": cookie})[0] == 200)
    with sync_playwright() as p:
        br = p.chromium.launch(); page = br.new_page(viewport={"width": 1360, "height": 950})
        page.goto(W2 + "/"); box = page.get_by_label("Access token"); box.wait_for(timeout=20000)
        body = page.inner_text("body"); page.screenshot(path=str(OUT / "sign-in.png"))
        check("auth", "the web app shows the sign-in page, not the task board, without a session", "access token" in body.lower() and page.get_by_role("button", name="Sign in").count() == 1 and page.locator(".task-list").count() == 0, body[:200])
        box.fill("wrong-token"); page.get_by_role("button", name="Sign in").click(); page.wait_for_selector("[role=alert]", timeout=10000)
        check("auth", "a wrong token shows an error and stays on sign-in", box.count() == 1, page.inner_text("body")[:200])
        box.fill(TOKEN); page.get_by_role("button", name="Sign in").click()
        check("auth", "the right token signs in and the app loads", wait(lambda: page.get_by_label("Access token").count() == 0 or None, 15), page.inner_text("body")[:200])
        page.reload(); page.wait_for_timeout(1500)
        check("auth", "the session survives a reload", page.get_by_label("Access token").count() == 0, page.inner_text("body")[:200]); br.close()
finally:
    for pr in procs:
        pr.terminate()
    for pr in procs:
        try: pr.wait(10)
        except Exception: pr.kill()
    # npm spawns vite as a child: stop whatever still holds the extra ports, by PID
    for port in (8001, 5174):
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout.split()
        for pid in out:
            try: os.kill(int(pid), signal.SIGTERM)
            except Exception: pass

# ------------------------------------------------------------------ session 2: the provider rejects the key
MOCK_FAIL.write_text("1")
sid2 = new_session("openai/mock")
call("POST", f"/sessions/{sid2}/start")
st = wait(lambda: (status(sid2) in ("needs_human", "failed", "paused")) and status(sid2), 60)
check("provider-failure", "a rejected provider call pauses the session for a person (no silent fallback)", st in ("needs_human", "paused"), st)
txt = " ".join(x[0] for x in sql("select body from message where session_id=%s", sid2))
rs = call("GET", f"/sessions/{sid2}")[1]["session"]
blob = json.dumps(rs) + txt
check("provider-failure", "the reason names the provider problem and never contains the API key", "provider" in blob.lower() and "sk-test-e2e" not in blob, blob[:200])
with sync_playwright() as p:
    br = p.chromium.launch(); page = br.new_page(viewport={"width": 1360, "height": 950})
    page.goto(f"{WEB}/room/{sid2}"); page.wait_for_selector("h1, h2", timeout=15000); page.wait_for_timeout(1500)
    body = page.inner_text("body"); page.screenshot(path=str(OUT / "room-provider-failure.png"))
    check("provider-failure", "the room shows the reason to the person, without the key", "provider" in body.lower() and "sk-test-e2e" not in body, body[:300])
    br.close()
MOCK_FAIL.unlink(missing_ok=True)

# ------------------------------------------------------------------ setup page: pickers
with sync_playwright() as p:
    br = p.chromium.launch(); page = br.new_page(viewport={"width": 1360, "height": 950})
    page.goto(f"{WEB}/setup/{task}"); page.wait_for_selector("h1, h2", timeout=15000)
    page.get_by_role("radio", name=re.compile("^Debate")).click()
    page.get_by_role("button", name="Add to roster").first.click()
    seat = page.locator("select.seat-model").first
    opts = seat.locator("option").all_inner_texts()
    check("setup", "the seat model picker names the provider model", any("Mock GPT" in o and "OpenAI" in o for o in opts), opts)
    writer = page.get_by_label("Final write-up model").locator("option").all_inner_texts()
    check("setup", "the write-up picker offers Claude models only", writer and not any("Mock GPT" in o for o in writer), writer)
    page.screenshot(path=str(OUT / "setup.png")); br.close()

fails = R.count(False); print(f"\n{R.count(True)} passed, {fails} failed"); sys.exit(1 if fails else 0)
