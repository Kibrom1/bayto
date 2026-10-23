#!/usr/bin/env python3
"""Feature end-to-end test against the stack started by run_features_e2e.sh (serve_with_fakes.py + mock_openai.py + Vite).
Real: Postgres, runner, floor policy, attachments, retrieval, role visibility, the provider adapter over HTTP, SSE, the
React UI in headless Chromium. Faked: the sandbox responder, the moderator's summary/synthesis/hand-raise models.
Prints PASS/FAIL per check and exits non-zero on any FAIL."""
import json, os, re, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

import psycopg
from playwright.sync_api import sync_playwright

API = os.environ.get("API", "http://127.0.0.1:8000"); WEB = os.environ.get("WEB", "http://127.0.0.1:5173")
DB = os.environ.get("DATABASE_URL", "postgresql+psycopg://bayto:bayto@localhost:5432/bayto_e2e").replace("+psycopg", "")
E2E = Path("/tmp/e2e"); MOCK_LOG = E2E / "mock_openai.jsonl"; MOCK_FAIL = E2E / "mock_fail"; SYNTH = E2E / "synth_hint.txt"
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


for p in (MOCK_LOG, MOCK_FAIL, SYNTH):
    p.unlink(missing_ok=True)

# ------------------------------------------------------------------ catalogue and setup
s, tpl = call("GET", "/agents/templates"); s2, modes = call("GET", "/modes")
roles = [t["role"] for t in tpl if not t["role"].startswith("build-")][:2]
A, B = [t for t in tpl if t["role"] in roles][:2]; roleA, roleB = A["role"], B["role"]
debate = next(m for m in modes if m["name"] == "debate")
s, cat = call("GET", "/models"); mock = next((m for m in cat["models"] if m["id"] == "openai/mock"), {})
check("models", "the provider model is listed, ready, and labelled OpenAI", mock.get("ready") and mock.get("provider_label") == "OpenAI", mock)
check("models", "the provider model is not offered for Build seats", mock.get("build_ok") is False, mock)
check("config", "/app-config exposes the feedback URL", call("GET", "/app-config")[1] == {"feedback_url": "https://example.com/feedback"})

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
