import time, re, json
from playwright.sync_api import sync_playwright
OUT="/tmp/tr/ut/rosa"
def note(k,t): print(f"{k}: {t}", flush=True)
AXE=open("/tmp/tr/node_modules/axe-core/axe.min.js").read()
with sync_playwright() as p:
    b=p.chromium.launch()
    pg=b.new_context().new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)[:200])); pg.on("console", lambda m: errs.append(m.text[:200]) if m.type=="error" else None)
    pg.route("**/tasks", lambda r: r.abort()); pg.goto("http://localhost:5173/"); pg.wait_for_timeout(2500)
    note("apidown","body=%r html len=%d errs=%s"%(pg.locator("body").inner_text()[:80], len(pg.content()), errs[:3]))
    # board row menu on finished task
    pg=b.new_context(viewport={"width":1280,"height":800}).new_page(); pg.set_default_timeout(5000)
    pg.goto("http://localhost:5173/"); pg.wait_for_timeout(1000)
    row=pg.locator(".task-row", has_text="hello.py")
    row.locator("summary").click(); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/50-more.png")
    note("menu","items=%s"%pg.locator("[role=menuitem]").all_inner_texts())
    pg.keyboard.press("ArrowDown"); note("menu","focus after ArrowDown: %s"%pg.evaluate("document.activeElement.innerText"))
    pg.keyboard.press("Escape")
    # View result -> room; refresh; back
    row.get_by_role("link", name="View result").click(); pg.wait_for_timeout(1500); u=pg.url; note("nav","view result -> %s"%u)
    pg.reload(); pg.wait_for_timeout(1500); note("refresh","room after reload: %s"%pg.locator(".room-title").inner_text())
    pg.go_back(); pg.wait_for_timeout(800); note("back","url=%s"%pg.url)
    # room a11y + long text
    pg.goto(u); pg.wait_for_timeout(1200); pg.evaluate(AXE)
    r=pg.evaluate("axe.run().then(r=>r.violations.map(v=>({id:v.id,impact:v.impact,n:v.nodes.length})))"); note("axe:room",json.dumps(r))
    note("room","landmarks main=%d nav=%d"%(pg.locator("main").count(), pg.locator("nav").count()))
    # mobile room
    m=b.new_context(viewport={"width":390,"height":800}, device_scale_factor=2, is_mobile=True, has_touch=True).new_page(); m.goto(u); m.wait_for_timeout(1500)
    m.screenshot(path=f"{OUT}/60-m-room.png"); m.screenshot(path=f"{OUT}/60b-m-room-full.png", full_page=True)
    note("mobile","room h-scroll=%s composer visible=%s"%(m.evaluate("document.documentElement.scrollWidth>innerWidth"), m.get_by_label("Interject").is_visible()))
    # delete via room (keyboard)
    pg.get_by_role("button", name="Delete").click(); pg.wait_for_timeout(300); pg.screenshot(path=f"{OUT}/70-delete-dialog.png")
    note("delete","focus in dialog: %s"%pg.evaluate("document.activeElement.innerText"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(200); note("delete","Esc closes: %s"%(pg.get_by_text("Delete this session?").count()==0))
    pg.get_by_role("button", name="Archive").click(); pg.wait_for_timeout(900); pg.screenshot(path=f"{OUT}/71-archived.png")
    note("archive","notice=%s buttons=%s"%(pg.locator(".notice").all_inner_texts(), pg.locator(".room-actions button").all_inner_texts()))
    pg.goto("http://localhost:5173/"); pg.wait_for_timeout(1000)
    note("archive","board row after archive: %s"%pg.locator(".task-row", has_text="hello.py").inner_text().replace("\n"," | "))
    # long text task
    pg.get_by_role("button", name="New task").click(); long="W"*120
    pg.get_by_placeholder("Weekly or biweekly releases?").fill("Very long title "+long); pg.get_by_placeholder("Context, constraints").fill("x "*1500); pg.get_by_placeholder("Pick one or type your own").fill("y")
    pg.get_by_role("button", name="Create task").click(); pg.wait_for_timeout(1200); pg.screenshot(path=f"{OUT}/80-long.png")
    note("long","h-scroll=%s"%pg.evaluate("document.documentElement.scrollWidth>innerWidth"))
    b.close()
