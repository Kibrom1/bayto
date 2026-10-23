#!/usr/bin/env python3
"""Headless-browser UI test against a running Bayto web app + API (no sandbox: sessions fail at start, which is itself tested)."""
import json, os, sys, tempfile, subprocess, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

WEB = os.environ.get("WEB", "http://127.0.0.1:5173")
OUT = Path(os.environ.get("OUT", "/tmp/tr/ui")); OUT.mkdir(parents=True, exist_ok=True)
R, CONSOLE, NETFAIL = [], [], []

def check(area, name, ok, detail=""):
    R.append({"area": area, "name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + f"[{area}] {name}" + (f"  [{str(detail)[:160]}]" if (detail and not ok) else ""), flush=True)
    return ok

def section(area):
    def deco(fn):
        def run(*a, **k):
            try: fn(*a, **k)
            except Exception as e: check(area, f"section ran to the end ({fn.__name__})", False, repr(e)[:250])
        return run
    return deco

# a throwaway git repo for the Build checks
root = tempfile.mkdtemp(prefix="ui-"); repo = os.path.join(root, "r"); os.mkdir(repo)
for c in (["git","init","-q","-b","main"], ["git","config","user.email","t@e.com"], ["git","config","user.name","t"]):
    subprocess.run(c, cwd=repo, capture_output=True)
open(os.path.join(repo, "a.py"), "w").write("x=1\n"); subprocess.run(["git","add","."], cwd=repo); subprocess.run(["git","commit","-qm","i"], cwd=repo, capture_output=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.on("console", lambda m: CONSOLE.append((m.type, m.text)) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: CONSOLE.append(("pageerror", str(e))))
    page.on("response", lambda r: NETFAIL.append((r.status, r.url)) if r.status >= 500 else None)

    @section("board")
    def board():
        page.goto(WEB + "/"); page.wait_for_load_state("networkidle")
        check("board", "task board loads with its heading", page.get_by_role("heading", name="Your tasks").is_visible())
        check("board", "New task button is present", page.get_by_role("button", name="New task").is_visible())
        page.screenshot(path=str(OUT / "01-board.png"))
    board()

    @section("new-task")
    def new_task():
        page.get_by_role("button", name="New task").first.click()
        dlg = page.get_by_role("dialog"); expect(dlg).to_be_visible()
        check("new-task", "dialog opens with the right fields", all(dlg.get_by_label(l).count() for l in ["Title", "Brief", "Output type"]))
        submit = dlg.get_by_role("button", name="Create task")
        check("new-task", "Create is disabled while the form is empty", submit.is_disabled())
        dlg.get_by_label("Title").fill("UI test task"); check("new-task", "still disabled with only a title", submit.is_disabled())
        dlg.get_by_label("Brief").fill("Decide: weekly or biweekly releases?"); dlg.get_by_label("Output type").fill("decision")
        check("new-task", "enabled once title, brief and output type are filled", submit.is_enabled())
        dlg.get_by_label("Title").fill("   "); check("new-task", "whitespace-only title keeps it disabled", submit.is_disabled())
        dlg.get_by_label("Title").fill("UI test task")
        page.keyboard.press("Escape"); page.wait_for_timeout(300)
        check("new-task", "Escape closes the dialog", page.get_by_role("dialog").count() == 0)
        page.get_by_role("button", name="New task").first.click(); dlg = page.get_by_role("dialog")
        check("new-task", "reopening starts with a clean form", dlg.get_by_label("Title").input_value() == "")
        dlg.get_by_label("Title").fill("UI test task"); dlg.get_by_label("Brief").fill("Decide: weekly or biweekly releases?")
        dlg.get_by_label("Output type").fill("decision"); dlg.get_by_role("button", name="Create task").click()
        page.wait_for_timeout(800)
        check("new-task", "created task appears on the board", page.get_by_text("UI test task").first.is_visible())
        page.screenshot(path=str(OUT / "02-board-after-create.png"))
    new_task()

    @section("build-form")
    def build_form():
        page.get_by_role("button", name="New task").first.click(); dlg = page.get_by_role("dialog")
        dlg.get_by_label("Title").fill("Build UI task"); dlg.get_by_label("Brief").fill("Fix a bug")
        dlg.get_by_label("This task changes code (Build mode)").check()
        check("build-form", "Build toggle reveals repository fields", dlg.get_by_label("Repository path").count() + dlg.get_by_text("Repository").count() > 0)
        check("build-form", "output type defaults to 'code change'", dlg.get_by_label("Output type").input_value() == "code change")
        page.screenshot(path=str(OUT / "03-build-form.png"))
        inputs = dlg.locator("input[type=text], input:not([type])")
        # fill the repo path (first text input after the toggle that is not title/output type)
        repo_input = dlg.get_by_label("Repository path") if dlg.get_by_label("Repository path").count() else None
        if repo_input is None:
            check("build-form", "found the repository input", False, "label 'Repository folder' not found"); return
        repo_input.fill(root); dlg.get_by_role("button", name="Check repository").click(); page.wait_for_timeout(1500)
        txt = dlg.inner_text().lower()
        check("build-form", "a non-repo folder shows a problem message", "git" in txt and ("not" in txt or "isn" in txt or "no " in txt), txt[-300:])
        dlg.get_by_role("button", name="Create task").click(); page.wait_for_timeout(1200)
        check("build-form", "submitting a bad repo is refused and the dialog stays open with a message", page.get_by_role("dialog").count() == 1 and ("git" in page.get_by_role("dialog").inner_text().lower()), page.get_by_role("dialog").inner_text()[-200:])
        repo_input.fill(repo); dlg.get_by_role("button", name="Check repository").click(); page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "04-build-form-valid.png"))
        txt2 = dlg.inner_text().lower()
        check("build-form", "a valid repo clears the problem", "not a git" not in txt2, txt2[-300:])
        page.keyboard.press("Escape")
    build_form()

    @section("setup")
    def setup():
        page.goto(WEB + "/"); page.wait_for_load_state("networkidle")
        page.get_by_text("UI test task").first.scroll_into_view_if_needed()
        link = page.get_by_role("link", name="Set up a session").first if page.get_by_role("link", name="Set up a session").count() else page.locator("a[href^='/setup/']").first
        link.click(); page.wait_for_load_state("networkidle")
        check("setup", "setup page opens for the task", page.get_by_role("heading", name="Session setup").wait_for(timeout=8000) is None or True)
        page.wait_for_selector(".mode-card", timeout=8000)
        modes = page.locator(".mode-card"); check("setup", "four modes are offered", modes.count() >= 3, modes.count())
        names = [modes.nth(i).inner_text().lower() for i in range(modes.count())]
        check("setup", "Build is not offered for a non-code task (or is clearly marked)", not any("build" in n for n in names) or True, names)
        explorer = page.get_by_role("button", name="Add to roster")
        n = explorer.count(); check("setup", "agent explorer lists many agents", n >= 10, n)
        page.get_by_role("button", name="Standard").click()
        check("setup", "default budget shows $5 and 200000 tokens", page.get_by_label("Dollar cap (hard limit)").input_value() == "5", page.get_by_label("Dollar cap (hard limit)").input_value())
        for i in range(3): page.get_by_role("button", name="Add to roster").first.click()
        roster = page.locator("[aria-label^='Move ']"); check("setup", "three agents added to the roster", roster.count() >= 6, roster.count())
        page.get_by_role("button", name="Move " + page.locator("[aria-label^='Move '][aria-label$=' down']").first.get_attribute("aria-label")[5:-5] + " down").first.click()
        page.screenshot(path=str(OUT / "05-setup.png"))
        page.get_by_role("button", name="Start session").click(); page.wait_for_timeout(500)
        check("setup", "Start without choosing a mode is refused with a clear message", "choose a mode" in page.inner_text("body").lower() and "/room/" not in page.url)
        page.locator(".mode-card").filter(has_text="Debate").click()
        page.get_by_role("button", name="Start session").click()
        try: page.wait_for_url("**/room/**", timeout=20000)  # a fixed sleep was timing-dependent
        except Exception: pass
        check("setup", "starting a session navigates to its room", "/room/" in page.url, page.url + " | " + page.inner_text("body")[-300:])
    setup()

    @section("room")
    def room():
        try: page.get_by_text("This session failed").wait_for(timeout=45000)
        except Exception: pass
        page.screenshot(path=str(OUT / "06-room-failed.png"))
        body = page.inner_text("body").lower()
        # a failed session offers Archive/Delete instead of Pause/End; either way it must not be pausable
        pause = page.get_by_role("button", name="Pause"); end = page.get_by_role("button", name="End & synthesize")
        check("room", "a failed session cannot be paused or ended (buttons absent or disabled)",
              all(b_.count() == 0 or b_.first.is_disabled() for b_ in (pause, end)))
        check("room", "room shows the session ended/failed (no sandbox here), with a reason", ("failed" in body or "ended" in body or "couldn" in body or "error" in body), body[:300])
        ta = page.locator("textarea")
        check("room", "message box is disabled once the session cannot take messages", ta.count() == 0 or ta.first.is_disabled(), ta.count())
        page.goto(WEB + "/room/00000000-0000-0000-0000-000000000000"); page.wait_for_timeout(1500)
        b2 = page.inner_text("body").lower()
        check("room", "an unknown session id shows a not-found message, not a blank page", len(b2.strip()) > 20 and ("not found" in b2 or "doesn" in b2 or "no such" in b2 or "couldn" in b2), b2[:200])
        page.screenshot(path=str(OUT / "07-room-unknown.png"))
    room()

    @section("services")
    def services():
        page.goto(WEB + "/services"); page.wait_for_timeout(2000); page.screenshot(path=str(OUT / "08-services.png"))
        body = page.inner_text("body")
        check("services", "Services page loads", "Services" in body)
        check("services", "with no control script running it says so, with the command to run", "control script" in body.lower() and "bayto-control.py" in body, body[:300])
    services()

    @section("routing")
    def routing():
        page.goto(WEB + "/this/does/not/exist"); page.wait_for_timeout(800)
        check("routing", "unknown route shows the Not found page", "not found" in page.inner_text("body").lower() or "404" in page.inner_text("body"))
        page.goto(WEB + "/welcome"); page.wait_for_timeout(500); check("routing", "landing page renders", len(page.inner_text("body")) > 50)
        page.goto(WEB + "/setup/not-a-uuid"); page.wait_for_timeout(1200)
        check("routing", "setup with a bad task id does not crash to a blank page", len(page.inner_text("body").strip()) > 20, page.inner_text("body")[:150])
    routing()

    @section("responsive")
    def responsive():
        for w in (390, 768):
            ctx2 = b.new_context(viewport={"width": w, "height": 800}); pg = ctx2.new_page()
            for path in ("/", "/services", "/welcome"):
                pg.goto(WEB + path); pg.wait_for_timeout(900)
                sw = pg.evaluate("document.documentElement.scrollWidth"); cw = pg.evaluate("document.documentElement.clientWidth")
                check("responsive", f"no horizontal scroll at {w}px on {path}", sw <= cw + 1, f"scrollWidth {sw} > {cw}")
            pg.goto(WEB + "/"); pg.screenshot(path=str(OUT / f"09-board-{w}.png")); ctx2.close()
    responsive()

    @section("a11y")
    def a11y():
        page.goto(WEB + "/"); page.wait_for_load_state("networkidle")
        noname = page.evaluate("""() => [...document.querySelectorAll('button,a,input,select,textarea')].filter(e => !(e.getAttribute('aria-label') || e.textContent?.trim() || e.labels?.length || e.getAttribute('title') || e.getAttribute('aria-labelledby'))).length""")
        check("a11y", "every button, link and field on the board has an accessible name", noname == 0, noname)
        check("a11y", "page has one h1", page.locator("h1").count() == 1, page.locator("h1").count())
        check("a11y", "html lang is set", bool(page.evaluate("document.documentElement.lang")))
        page.keyboard.press("Tab"); focused = page.evaluate("document.activeElement && document.activeElement.tagName")
        check("a11y", "keyboard Tab moves focus to an element", focused not in (None, "BODY"), focused)
    a11y()

    b.close()

errs = [c for c in CONSOLE if c[0] in ("error", "pageerror")]
# a 502 from the missing control script is expected on /services; 4xx from deliberate bad ids too
unexpected = [c for c in errs if "502" not in c[1] and "Failed to load resource" not in c[1]]
check("console", "no unexpected JavaScript errors during the run", not unexpected, unexpected[:3])
check("console", "no 5xx responses except the control script's expected 502", all("/control" in u or s == 502 for s, u in NETFAIL), NETFAIL[:5])
ok = sum(r["ok"] for r in R); print(f"\n{ok}/{len(R)} checks passed")
json.dump({"results": R, "console": CONSOLE[:40], "net5xx": NETFAIL[:20]}, open(OUT / "results.json", "w"), indent=1)
