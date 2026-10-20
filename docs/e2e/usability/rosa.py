import time, re, json
from playwright.sync_api import sync_playwright
OUT="/tmp/tr/ut/rosa"; t0=time.time()
def note(k,t): print(f"[{time.time()-t0:6.1f}] {k}: {t}", flush=True)
AXE=open("/tmp/tr/node_modules/axe-core/axe.min.js").read()
def axe(pg,label):
    pg.evaluate(AXE); r=pg.evaluate("axe.run().then(r=>r.violations.map(v=>({id:v.id,impact:v.impact,n:v.nodes.length,help:v.help})))")
    note("axe:"+label, json.dumps(r)); return r
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1280,"height":800}); pg=ctx.new_page(); pg.set_default_timeout(6000)
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    shot=lambda n,**k: pg.screenshot(path=f"{OUT}/{n}.png",**k)
    def step(name, fn):
        try: fn()
        except Exception as e: note("FAIL", f"{name}: {str(e).splitlines()[0][:160]}")
    pg.goto("http://localhost:5173/"); pg.wait_for_timeout(1200); shot("01-board")
    note("see","rows=%d"%pg.locator(".task-row").count())
    axe(pg,"board")
    # search
    step("search", lambda: (pg.get_by_role("searchbox").first.fill("hiring"), pg.wait_for_timeout(400)))
    note("see","search 'hiring' rows=%d"%pg.locator(".task-row").count()); shot("02-search")
    pg.get_by_role("searchbox").first.fill("zzzz"); pg.wait_for_timeout(300); note("see","no-match text: %s"%pg.locator("body").inner_text()[-200:].replace("\n"," | ")); shot("02b-nomatch")
    pg.get_by_role("searchbox").first.fill("")
    # filters
    for f in ["Live","Finished","Not started","All"]:
        step("filter"+f, lambda f=f: pg.get_by_role("button", name=re.compile("^"+f)).first.click()); pg.wait_for_timeout(200); note("see","filter %s rows=%d"%(f,pg.locator(".task-row").count()))
    # keyboard-only: new task
    pg.goto("http://localhost:5173/"); pg.wait_for_timeout(800)
    order=[]
    for i in range(14):
        pg.keyboard.press("Tab"); order.append(pg.evaluate("(()=>{const e=document.activeElement;return (e.innerText||e.getAttribute('aria-label')||e.tagName).slice(0,30).replace(/\\n/g,' ')})()"))
    note("kbd","tab order on board: %s"%order)
    step("kbd-open", lambda: pg.get_by_role("button", name="New task").focus()); pg.keyboard.press("Enter"); pg.wait_for_timeout(400)
    note("kbd","focus after opening dialog: %s"%pg.evaluate("document.activeElement.placeholder||document.activeElement.tagName"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300); note("kbd","Esc closes dialog: %s; focus back on: %s"%(pg.locator("[role=dialog],dialog[open]").count()==0, pg.evaluate("(document.activeElement.innerText||document.activeElement.tagName).slice(0,20)")))
    pg.get_by_role("button", name="New task").click(); pg.wait_for_timeout(300); axe(pg,"dialog")
    # tab trap?
    seen=set()
    for i in range(14): pg.keyboard.press("Tab"); seen.add(pg.evaluate("document.activeElement.closest('[role=dialog],dialog')?'in':'out'"))
    note("kbd","dialog focus trap (in/out seen): %s"%seen)
    pg.keyboard.press("Escape")
    # empty create validation
    pg.get_by_role("button", name="New task").click(); note("see","Create task enabled when empty: %s"%pg.get_by_role("button", name="Create task").is_enabled()); shot("03-empty-create")
    note("see","empty create -> %s"%pg.locator("[role=dialog] [role=alert], dialog [role=alert], .alert, .field-error").all_inner_texts())
    pg.keyboard.press("Escape")
    # board row menu
    step("more", lambda: pg.locator(".task-row").first.get_by_role("button", name=re.compile("More")).click()); pg.wait_for_timeout(300); shot("04-more-menu")
    note("see","menu items=%s"%pg.locator("[role=menu] [role=menuitem], .row-menu button").all_inner_texts())
    pg.keyboard.press("Escape"); pg.wait_for_timeout(200); note("see","menu closed on Esc: %s"%(pg.locator("[role=menu]").count()==0))
    # Open the finished Maya/Daniel session, archive via board menu
    # mobile
    m=b.new_context(viewport={"width":390,"height":800}, device_scale_factor=2, has_touch=True, is_mobile=True); mp=m.new_page(); mp.set_default_timeout(6000)
    mp.goto("http://localhost:5173/"); mp.wait_for_timeout(1200); mp.screenshot(path=f"{OUT}/10-m-board.png")
    note("mobile","board h-scroll: %s"%mp.evaluate("document.documentElement.scrollWidth>innerWidth"))
    tid=pg.evaluate("1")
    mp.get_by_role("link", name=re.compile("Start session")).first.click(); mp.wait_for_timeout(1200); mp.screenshot(path=f"{OUT}/11-m-setup.png", full_page=True)
    note("mobile","setup h-scroll: %s"%mp.evaluate("document.documentElement.scrollWidth>innerWidth"))
    url=mp.url; axe(mp,"setup-mobile")
    # tap targets
    small=mp.evaluate("[...document.querySelectorAll('button,a,input,select')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.height<40||r.width<40)}).map(e=>(e.innerText||e.getAttribute('aria-label')||e.tagName).slice(0,20)+':'+Math.round(e.getBoundingClientRect().width)+'x'+Math.round(e.getBoundingClientRect().height)).slice(0,12)")
    note("mobile","targets <40px on setup: %s"%small)
    # dark
    d=b.new_context(viewport={"width":1280,"height":800}, color_scheme="dark"); dp=d.new_page()
    dp.goto("http://localhost:5173/"); dp.wait_for_timeout(1200); dp.screenshot(path=f"{OUT}/20-dark-board.png")
    dp.goto(url.replace(":5173",":5173")); dp.wait_for_timeout(1200); dp.screenshot(path=f"{OUT}/21-dark-setup.png")
    axe(dp,"dark-setup")
    # 200% zoom equivalent
    z=b.new_context(viewport={"width":640,"height":400}, device_scale_factor=2); zp=z.new_page(); zp.goto("http://localhost:5173/"); zp.wait_for_timeout(1200); zp.screenshot(path=f"{OUT}/30-zoom-board.png")
    note("zoom","board h-scroll at 640x400: %s"%zp.evaluate("document.documentElement.scrollWidth>innerWidth"))
    # failure modes: API down via route abort
    pg2=b.new_context().new_page(); pg2.route("**/tasks*", lambda r: r.abort()); pg2.goto("http://localhost:5173/"); pg2.wait_for_timeout(2000); pg2.screenshot(path=f"{OUT}/40-api-down.png")
    note("fail","API down board text: %s"%pg2.locator("body").inner_text()[-300:].replace("\n"," | "))
    pg3=b.new_context().new_page(); pg3.goto("http://localhost:5173/room/does-not-exist"); pg3.wait_for_timeout(2000); pg3.screenshot(path=f"{OUT}/41-bad-room.png")
    note("fail","bad room: %s"%pg3.locator("body").inner_text()[-300:].replace("\n"," | "))
    pg3.goto("http://localhost:5173/nonsense/path"); pg3.wait_for_timeout(1000); note("fail","unknown route: %s"%pg3.locator("body").inner_text()[-200:].replace("\n"," | "))
    note("see","js errors=%s"%errs[:5]); b.close()
