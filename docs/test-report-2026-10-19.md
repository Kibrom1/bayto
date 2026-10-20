# Test report: 2026-10-19

A full test pass over Bayto on commit `cc4cc66` (plus the two fixes this pass produced). What was run, what passed, what broke, and what could not be tested from where it ran.

## Summary

- **Everything that could run in the cloud copy passed**, after two defects were fixed (below). 469 orchestrator tests, 162 agent-comms tests, 43 web tests, type check, build, and dependency audits are clean. A black-box API test (61 checks) and a headless-browser UI test (44 checks) pass, apart from three API expectations I judged to be my own mistakes, not defects.
- **Two real defects found and fixed:** the test suite hung in about 3 of 8 full runs (a test-isolation bug), and the workspace "Check repository" step said "ready" for a test command that Create would then refuse.
- **Not covered here:** anything that needs `sbx`, a real model, or your Mac: live agent sessions, Build runs, sandbox stop and resume, the control script on your machine. Build was run live on 2026-10-18 (B9, three runs, see `work-plan.md`). The Services-page 502 you reported on your Mac is not explained yet (see Open items).

## Environment

Cloud container, Linux, Python 3.13, Node 22, Postgres 16, headless Chromium (Playwright). No `sbx`, no Docker, no `claude` login. The API ran against its own throwaway database (`bayto_e2e`) with the real app, real migrations and the seeded modes and agents. Sessions cannot start a sandbox here, so a session start fails cleanly; that is tested as a feature, not skipped.

## 1. Automated suites and static checks

| Check | Result |
| --- | --- |
| Orchestrator `pytest` (Postgres, `BAYTO_REQUIRE_DB=1`) | 469 passed. Three full runs in a row after the hang fix, 114 to 121 s each |
| agent-comms `pytest` | 162 passed, 1 xpassed |
| Web `vitest` | 43 tests in 8 files passed |
| Web `tsc -b` (type check) | clean |
| Web `oxlint` | 0 errors, 4 warnings (fast-refresh export style in 3 files; one missing hook dependency in `SessionSetup.tsx`) |
| Web production build | built; JS 321.5 kB, CSS 38.9 kB |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `pip-audit` on the orchestrator's installed packages | no known vulnerabilities (the two local packages, `acp` and `bayto-orchestrator`, cannot be audited from PyPI) |

## 2. Black-box API test (`docs/e2e/smoke/api_blackbox.py`)

61 checks against the running API: 58 pass, 3 are judged expectation mistakes (below). Areas: health, catalogue (4 modes, 17 agent templates), tasks (create, validation, odd characters, list, usage report), workspaces (real throwaway git repos: clean, not a repo, missing folder, missing branch, dirty tree, shell-injection test commands, relative path), sessions (create, 9 kinds of bad input, Build rules, fetch, 404 and 422), lifecycle on a session that was never started, session start without a sandbox, and the event stream.

What it confirms: every bad input gets a 4xx with a reason, never a 500; odd characters (`'; DROP TABLE task;--`, a script tag) are stored and shown as plain text; test commands with `;`, `|` or `$(...)` are refused when a task is created; a start with no `sbx` ends the session as `failed` with a stated reason.

| Finding | Verdict |
| --- | --- |
| Preflight said `ok: true` for a test command that task creation refuses | **Defect, fixed** (`fcf15fc`, test added) |
| A Build session can be created through the API with fewer than four roles | By design: the setup page blocks it ("Build needs all four roles"), and a missing seat at run time pauses the session for a person. A direct API client is not stopped at creation. Noted, not changed |
| Muting an agent that is not in a never-started session answers 409 (session not live) rather than 404 | Fine: the state check comes first |
| The event stream for an unknown session answers 200 and ends at once with an `end` event, rather than 404 | Benign; the room page checks the session first. Noted |

## 3. Headless-browser UI test (`docs/e2e/smoke/ui_test.py`)

44 of 44 checks pass at the end (an earlier pass had misses that were my script's wrong labels, fixed before this count). Chromium, 1280 px, plus 390 px and 768 px for layout.

- **Task board:** loads, New task button, the created task appears.
- **New task form:** Create is disabled until title, brief and output type are filled; a whitespace-only title keeps it disabled; Escape closes; reopening gives a clean form.
- **Build form:** the code toggle reveals repository fields and defaults the output type to "code change"; Check repository reports a non-repo folder; Create refuses a bad repository and stays open with the message; a valid repository clears the problem.
- **Session setup:** four modes, the agent explorer lists the agents, the default budget is $5 / 200,000 tokens, three agents join the roster, Start without a mode is refused with "Choose a mode", and choosing Debate and Start opens the room.
- **Room:** on a failed session (no sandbox here) the room shows "This session failed" with the reason, the message box and the Pause and End buttons are disabled; an unknown session id shows a not-found message.
- **Services page:** with no control script running it says so and names the command to run. With the script running it connects (tested separately, see Open items).
- **Routing:** unknown routes, the landing page and a bad task id show a page, not a blank screen.
- **Layout and accessibility:** no horizontal scroll at 390 px or 768 px on the board, Services or landing pages; every button, link and field on the board has an accessible name; one `h1`; `lang` is set; Tab moves focus.
- **Console:** no unexpected JavaScript errors and no 5xx responses (other than the control script's expected 502 when it is not running).

UX observations (not defects): the roster panel is empty while a session is in the "Starting" state, and the failure reason on a failed start shows the raw `dev-team/run.sh` output.

## 4. Defects found and fixed in this pass

1. **The orchestrator suite hung in about 3 of 8 full runs** (commit `add09b1`). One API test removed a running session from the app's registry, so the fixture could not cancel its mirror and runner tasks. They kept a transaction open, and the next test's `DROP TABLE` waited on that lock forever. Found by looking at the database locks of a stuck run. Fixed in the test, and the schema teardown now ends any other connections so a future leak fails loudly instead of hanging. Verified by three consecutive clean full runs.
2. **Preflight and task creation disagreed about test commands** (commit `fcf15fc`). See the API findings.

Both fixes are on the branch `fix/test-hang-and-preflight`, stacked on `feat/turn-cost-warning`.

## 5. What was not tested, and why

| Not tested | Why | Where it stands |
| --- | --- | --- |
| Live agent sessions in debate, open chat, brainstorm | Needs `sbx` and a real model | `docs/e2e/live/run_live_e2e.py` on your Mac; MVP exit test (`mvp-exit-checklist.md`) |
| Build with a real team | Same | Three live runs on 2026-10-18 passed or paused as designed (`work-plan.md` B9) |
| Sandbox stop then Resume (M1.14 follow-up) | Needs `sbx` | Open precondition of the exit test |
| The "Cost check" note and the note-race fix in a live session | Covered by unit and runner tests only | Will show in your next live run |
| Ollama fallback network path | Needs a seat and Ollama | Open |
| Load, many concurrent sessions, long sessions | Out of scope for this pass | Not measured |
| Authentication | There is none (documented) | Do not expose the ports |

## 6. Open items from this pass

1. **Control panel 502 on your Mac.** The Services page shows "The control script isn't reachable" when nothing answers on port 8787. In the cloud copy the script starts cleanly, answers on 8787, and the page connects through the web proxy, so the code path works. On your Mac something differs: the script is not running, has exited, or port 8787 is held by an old process. Needs the output of `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/`, `lsof -nP -iTCP:8787 -sTCP:LISTEN`, and the script's own terminal output.
2. The one small known gap on the API: Build role validation only in the UI.
3. The four lint warnings.

## 7. Reproducing this

```
# suites
cd orchestrator && BAYTO_REQUIRE_DB=1 python -m pytest -q          # needs Postgres at DATABASE_URL
cd agent-comms  && python -m pytest -q
cd web && npx tsc -b && npm test && npx oxlint && npm run build && npm audit --omit=dev

# black-box API and UI (own database, own ports)
createdb bayto_e2e ; cd orchestrator && DATABASE_URL=...bayto_e2e alembic upgrade head && python -m orchestrator.seed
BAYTO_SUMMARY_MODEL=x BAYTO_SYNTHESIS_MODEL=x uvicorn orchestrator.app:app --port 8000 &
cd web && npx vite --port 5173 &
python3 docs/e2e/smoke/api_blackbox.py
python3 docs/e2e/smoke/ui_test.py          # needs: pip install playwright && playwright install chromium
```
