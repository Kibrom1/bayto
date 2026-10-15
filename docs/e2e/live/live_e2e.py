#!/usr/bin/env python3
"""Scripted real-session check for Bayto. Drives the real web UI with Playwright and uses the API as ground truth.

Runs against a LIVE stack (real sbx seats, real `claude -p` moderator). Keep this file OUTSIDE the Bayto repo:
it is an independent tester and only talks to the running app over HTTP.

Setup (once):  pip install playwright && playwright install chromium
Run:           python live_e2e.py --mode debate            # then:  python live_e2e.py --mode open-chat
Useful flags:  --headed   watch the browser
               --seats 3
               --natural-finish   (debate) let the session end at max_rounds instead of pressing End
Exit status is 0 only if every check passed. Screenshots, a JSON report and the final API state land in --out.
"""
import argparse, json, re, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--web", default="http://127.0.0.1:5173")
ap.add_argument("--api", default="http://127.0.0.1:8000")
ap.add_argument("--mode", default="debate", help="mode: debate | open-chat | brainstorm")
ap.add_argument("--seats", type=int, default=3)
ap.add_argument("--headed", action="store_true")
ap.add_argument("--natural-finish", action="store_true")
ap.add_argument("--start-timeout", type=int, default=900, help="seconds for sandboxes to build and the first run to start")
ap.add_argument("--turn-timeout", type=int, default=240, help="seconds to wait for a turn")
ap.add_argument("--resume-timeout", type=int, default=240, help="seconds to wait for a turn after Resume; >120 usually means the grant-timeout bug")
ap.add_argument("--stop-timeout", type=int, default=300)
ap.add_argument("--out", default="live-e2e-out")
args = ap.parse_args()

out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
results, reqs, errs = [], [], []


def check(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return ok


def get(path):
    with urllib.request.urlopen(args.api + path, timeout=30) as r:
        return json.load(r)


def session(sid):
    return get(f"/sessions/{sid}")["session"]


def turns(sid):
    return sum(t["turns"] for t in session(sid).get("turn_counts", []))


def wait(cond, timeout, every=2.0):
    """Poll cond() until truthy; returns the value or None. A session that reaches `failed` ends the wait."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(every)
    return None


def main():
    sid = None
    with sync_playwright() as p:
        import os
        extra = {"executable_path": os.environ["E2E_CHROMIUM"], "args": ["--no-sandbox", "--disable-gpu"]} if os.environ.get("E2E_CHROMIUM") else {}
        br = p.chromium.launch(headless=not args.headed, **extra)
        ctx = br.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        pg.on("request", lambda r: reqs.append(r.url) if "/events" in r.url else None)
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("console", lambda m: errs.append("console.error: " + m.text[:200]) if m.type == "error" else None)

        def shot(name):
            pg.screenshot(path=str(out / f"{name}.png"))

        # ---- 1. create a task and start a session through the UI
        pg.goto(args.web + "/")
        pg.get_by_role("button", name="New task").click(); pg.wait_for_timeout(300)
        pg.get_by_label("Title").fill("Weekly or biweekly releases?")
        pg.get_by_label("Brief").fill("Decide the release cadence for the next quarter. Give concrete reasons.")
        pg.get_by_label("Output type").fill("decision")
        pg.get_by_role("button", name="Create task").click(); pg.wait_for_timeout(1000)
        (pg.get_by_role("link", name="Start session").first if pg.get_by_role("link", name="Start session").count()
         else pg.get_by_role("button", name="Start session").first).click()
        pg.wait_for_timeout(800)
        pg.locator("label.mode-card", has_text=re.compile(re.escape(args.mode).replace("\\-", "[- ]").replace("\\ ", "[- ]"), re.I)).locator("input").check(force=True)
        for _ in range(args.seats):
            pg.get_by_role("button", name="Add to roster").first.click(force=True)
        pg.wait_for_timeout(300); shot("1-setup")
        pg.get_by_role("button", name=re.compile("start session", re.I)).click(force=True)
        pg.wait_for_url(re.compile("/room/"), timeout=30000)
        sid = pg.url.split("/room/")[1].split("?")[0]
        check("setup flow reaches the room", True, sid)

        # ---- 2. sandboxes build, runner starts
        def active_or_failed():
            s = session(sid)
            return s if s["status"] in ("active", "failed", "finished", "cancelled") else None
        s = wait(active_or_failed, args.start_timeout, 3)
        check("session becomes active", s and s["status"] == "active",
              json.dumps({k: s.get(k) for k in ("status", "failure_reason", "stage")}) if s else f"still starting after {args.start_timeout}s")
        if not s or s["status"] != "active":
            shot("fail-start"); return sid

        # ---- 3. real turns arrive
        first = wait(lambda: turns(sid) >= 2 or session(sid)["status"] == "failed", args.turn_timeout)
        check("first two turns complete", turns(sid) >= 2, f"turns={turns(sid)} status={session(sid)['status']}")
        if session(sid)["status"] == "failed":
            shot("fail-turns"); return sid
        pg.wait_for_timeout(2000); shot("2-live")
        body = pg.inner_text("body")
        check("protocol messages hidden from the transcript",
              "Recent turns:" not in body and '"type": "x-' not in body and "x-stop" not in body, "")
        n_msgs = pg.locator(".msg:not(.live)").count()
        check("transcript shows finished messages", n_msgs >= 1, f"{n_msgs} messages in the room")
        d = session(sid)
        check("usage meter moves", (d.get("usage") or {}).get("tokens_in", 0) > 0, json.dumps(d.get("usage")))
        speakers = [t for t in d.get("turn_counts", []) if t["turns"] > 0]
        if "open" in args.mode.lower():
            wait(lambda: len([t for t in session(sid).get("turn_counts", []) if t["turns"] > 0]) >= 2, args.turn_timeout)
            sp = [t for t in session(sid).get("turn_counts", []) if t["turns"] > 0]
            check("raise-hand: more than one agent gets the floor", len(sp) >= 2, json.dumps(sp))

        # ---- 4. interject, reload
        pg.locator("textarea").first.fill("Human here: please also weigh the cost of rolling back a bad release.")
        pg.get_by_role("button", name="Send").click(); pg.wait_for_timeout(2500)
        check("interject appears in the transcript", "cost of rolling back" in pg.inner_text("body"))
        before = pg.locator(".msg:not(.live)").count()
        pg.reload(); pg.wait_for_timeout(5000)
        texts = [t.strip() for t in pg.locator(".msg:not(.live) .msg-body").all_inner_texts() if t.strip()]
        check("reload restores the transcript", len(texts) >= before and any("cost of rolling back" in t for t in texts),
              f"before={before} after={len(texts)}")
        check("reload shows no duplicate messages", len(texts) == len(set(texts)), f"{len(texts)} messages")

        # ---- 5. pause / resume
        pg.get_by_role("button", name="Pause").click()
        st = wait(lambda: session(sid)["status"] == "needs_human", 120)
        check("pause: status becomes needs_human", st, session(sid)["status"])
        t1 = turns(sid); pg.wait_for_timeout(20000); t2 = turns(sid)
        check("pause: no new turns are granted", t2 <= t1 + 1, f"turns {t1} -> {t2}")
        shot("3-paused")
        resume = pg.get_by_role("button", name="Resume")
        check("resume button is offered", resume.count() > 0)
        if resume.count():
            resume.click(); t0 = time.time()
            ok = wait(lambda: turns(sid) > t2 or session(sid)["status"] in ("failed", "finished"), args.resume_timeout)
            dt = time.time() - t0
            check("resume: the discussion continues", turns(sid) > t2,
                  f"turns {t2} -> {turns(sid)} in {dt:.0f}s; status={session(sid)['status']}; stop_reason={session(sid).get('stop_reason')}"
                  + ("  (slow: check for the grant-timeout bug)" if dt > 120 else ""))
            shot("4-resumed")

        # ---- 6. offline / online
        ctx.set_offline(True); pg.wait_for_timeout(3000); ctx.set_offline(False); pg.wait_for_timeout(12000)
        texts = [t.strip() for t in pg.locator(".msg:not(.live) .msg-body").all_inner_texts() if t.strip()]
        check("reconnect after offline: no duplicates", len(texts) == len(set(texts)), f"{len(texts)} messages")

        # ---- 7. finish
        if args.natural_finish:
            fin = wait(lambda: session(sid)["status"] in ("finished", "failed", "cancelled"), 3600, 5)
            check("session finishes by itself at its round limit", session(sid)["status"] == "finished", session(sid)["status"])
        else:
            if session(sid)["status"] == "active":
                pg.get_by_role("button", name=re.compile("End . synthesize")).click(); pg.wait_for_timeout(500)
                pg.get_by_role("button", name="End session", exact=True).click()
            t0 = time.time()
            fin = wait(lambda: session(sid)["status"] in ("finished", "failed", "cancelled"), args.stop_timeout)
            check("End & synthesize reaches finished", session(sid)["status"] == "finished",
                  f"{session(sid)['status']} after {time.time() - t0:.0f}s")
        pg.wait_for_timeout(6000); shot("5-final")
        try:
            arts = get(f"/sessions/{sid}/artifacts")
            blob = json.dumps(arts)
            check("synthesis artifact exists and is non-trivial", len(blob) > 200, f"{len(blob)} bytes")
        except Exception as e:  # noqa: BLE001
            check("synthesis artifact exists and is non-trivial", False, e)
        fb = pg.get_by_role("button", name="Pause")
        check("controls disabled when the session is over", (fb.is_disabled() if fb.count() else True))
        n1 = len(reqs); pg.wait_for_timeout(10000)
        check("no SSE reconnect storm after the end", len(reqs) - n1 <= 1, f"{len(reqs) - n1} new /events requests in 10s")
        br.close()
    return sid


sid = None
try:
    sid = main()
except Exception as e:  # noqa: BLE001
    check("script ran to completion", False, repr(e))
finally:
    if sid:
        try:
            (out / "final-session.json").write_text(json.dumps(session(sid), indent=2))
        except Exception:  # noqa: BLE001
            pass
    (out / "report.json").write_text(json.dumps({"results": results, "browser_errors": errs[:20]}, indent=2))
    failed = [r for r in results if not r["ok"]]
    print(f"\nbrowser errors: {errs[:6]}")
    print(f"SUMMARY {len(results) - len(failed)} passed, {len(failed)} failed. Session: {sid}. Report: {out / 'report.json'}")
    if failed:
        print("If something failed, send: report.json, final-session.json, the screenshots, the API log tail "
              "and orchestrator/.sessions/<session id>/")
sys.exit(1 if any(not r["ok"] for r in results) else 0)
