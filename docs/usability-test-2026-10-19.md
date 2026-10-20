# Bayto usability test, 2026-10-19

Method: three personas, played by Claude, drove the real web app (Vite on :5173) and real orchestrator (FastAPI on :8000, Postgres) with Playwright. Agents were **scripted fakes** (no real model, no real `sbx` sandbox), so the test covers the interface and the orchestration flow, not agent quality. Scripts and screenshots are in `docs/e2e/usability/`. Chromium only. Personas are simulated, so treat timings as relative, not as measured human speed.

## Personas and tasks

| Persona | Who | Task | Result |
|---|---|---|---|
| **Maya**, first-time PM | Never seen Bayto, no repo | Create a task, run a Debate with three agents, interject, read the outcome | Completed in ~46 s, 0 errors. Setup took 5 s. |
| **Daniel**, senior engineer | Wants code changed, cares about cost and safety | Build task on a git repo; the architect blocks once; answer; resume; review the result | Completed in ~24 s after the block. Branch, test run and diff stats were shown. |
| **Rosa**, ops lead | Many tasks, keyboard and phone | Search/filter five tasks, keyboard-only flows, archive, delete dialog, phone, dark, zoom, failures | All flows worked. Accessibility and recovery gaps below. |

## What works well
- First screen explains the product in one sentence and has one obvious action.
- Setup validation is clear ("Choose a mode. Add at least two agents…"); Build presets the four-role roster, so Daniel could start immediately.
- The blocked Build pause explains itself, accepts an answer while paused, and Resume continues with that answer (the earlier loop is gone).
- The Build "Change set" card is excellent: copyable `git switch` command, "nothing was pushed", test exit code, diff stats.
- The Debate synthesis (decision callout, key points, next steps, minority report, copy/download) is strong.
- Archive/delete: Delete confirm focuses the safe button ("Keep it"), Esc cancels; archived notice states the LLM-context effect.
- No horizontal scroll at 390 px, at 640×400 (200% zoom equivalent), or with a 120-character title and a 3,000-character brief. Dark mode is consistent. Bad room ID and unknown route show friendly pages. API-down board shows "Couldn't load tasks… Retry".
- No JavaScript errors in any run.

## Findings, ranked

Severity: **High** blocks or misleads a user; **Med** causes confusion or friction; **Low** polish.

| # | Sev | Finding | Evidence | Heuristic |
|---|---|---|---|---|
| 1 | High | **The result is hidden when a live session ends.** The transcript stays auto-scrolled to the bottom, so Maya sees the last repeated chat messages, and Daniel does not see the Change-set card (it sits at the top). Reloading shows the result first, which proves it is a scroll problem. | `maya/12-end.png` vs `maya/14-room-landing.png`; `daniel/10-end.png` vs `rosa/62-d-room-landing.png` | Visibility of system status |
| 2 | High | **Keyboard and screen reader: no `<main>` landmark on the board and setup pages** (axe: moderate, 11 content nodes outside landmarks); the room has a `<main>` that is not top level; duplicate banner on setup. The "Skip to content" target does not map to a main landmark. | axe on board, setup (mobile and dark), room | Accessibility (WCAG 1.3.1, 2.4.1) |
| 3 | Med | **Dialog focus handling.** Tab leaves the New task dialog (focus reached elements outside it), and after Esc focus does not return to the "New task" button. | Rosa keyboard run | WCAG 2.4.3, 3.2 |
| 4 | Med | **"Offline" shown for paused and finished sessions.** The live indicator reads like a connection fault when the session is simply not streaming. | header on every ended/paused screenshot | Match with the real world |
| 5 | Med | **Dead actions on ended sessions.** "Pause" and a pale "End & synthesize" stay in the header after Finished and Archived; Archived also stacks two banners ("Archived…" plus "Session ended…") and a toast. | `maya/12-end.png`, `rosa/71-archived.png` | Error prevention, minimalism |
| 6 | Med | **Raw `VERDICT: …` lines appear in chat and in the Moderator summary**, and the summary is cut mid-word ("What should t"). Machine protocol leaks to people. | `daniel/08-state.png` | Match with the real world |
| 7 | Med | **Board mislabels Build tasks:** a finished Build task shows "Synthesis ready / View result"; a Build session archived from the room leaves the task "Not started" with no hint that a branch exists. | `rosa/10-m-board.png`, archive run | Consistency |
| 8 | Med | **Axe "label-content-name-mismatch" (serious).** One control's accessible name does not contain its visible text (likely the row "More" summary, `aria-label="More actions for …"`) and the room has a scrollable region that is not keyboard-focusable (serious). | axe board and room | WCAG 2.5.3, 2.1.1 |
| 9 | Med | **Phone: touch targets under 40 px** (Add to roster 28 px, nav links 29 px, ← Tasks 34 px). On the phone board "More" is plain text beside two buttons, easy to miss. | mobile run; `rosa/10-m-board.png` | Fitts / WCAG 2.5.8 |
| 10 | Low | **Budget empty until a mode is picked**; presets Quick/Standard/Deep have no stated dollar values; estimate shows ~$0.00 at first. | `maya/05-setup.png` | Recognition over recall |
| 11 | Low | **Create task button is disabled with no explanation** when fields are empty. | Rosa dialog run | Error prevention |
| 12 | Low | **"Removed -0"** in the Change-set stats (should be 0). | `rosa/62-d-room-landing.png` | Polish |
| 13 | Low | Task row menu is a `<details>`: ArrowDown does not move into menu items; items are not `role=menuitem`. | Rosa menu run | WCAG 4.1.2 |
| 14 | Low | Repository path placeholder is `/Users/you/code/my-project`; no check that the path exists until Start. | Daniel form | Error prevention |

Not findings: repeated agent lines in the debate are an artifact of the scripted fake agents.

## Recommendations (to discuss; nothing changed yet)
1. On session end, scroll the room to the top (or pin the result card) and flash a "Result ready" notice. Covers #1.
2. Add a real `<main id="main">` in the app shell, fix the duplicate banner, make the room's scroll region focusable; trap and restore focus in dialogs. Covers #2, #3, #8.
3. Replace "Offline" with state-aware text ("Paused", "Ended", "Reconnecting…" only on a real drop). Hide Pause/End when ended. Show one banner. Covers #4, #5.
4. Strip the `VERDICT:` line from display, summarize at sentence boundaries. Covers #6.
5. Build tasks: "Change ready for review" label on the board, linking to the room; `0` not `-0`. Covers #7, #12.
6. Raise phone tap targets to 44 px; give "More" a button look. Covers #9.
7. Preselect Standard budget when a mode is chosen and show its dollar value; explain the disabled Create button. Covers #10, #11.

## Not covered
Real model output quality and latency; real `sbx` sandboxes and the Services control script; Safari/Firefox; screen-reader listening (axe only); real users. Refresh mid-session and Back were checked only on a finished session. Still worth a real Build run on your machine.

## Reproduce
`docs/e2e/usability/reset.sh` (resets a scratch DB `bayto_ux` and starts `run_fake.py` on :8000), start Vite, then run `maya.py`, `daniel.py`, `rosa.py`, `rosa2.py`. The scripts assume `/tmp/tr/...` paths and Playwright; adjust before reuse.

## Fix status (2026-10-20, branch `fix/usability-2026-10-19`)
Fixed and re-tested live (axe now reports zero violations on board, room and setup; 52 web tests pass): #1 result shown when a live session ends, #2 and #8 `<main>` landmark, focusable transcript, brand-link name, #4 "Offline" removed (only "Reconnecting…" on a real drop), #5 Pause/End hidden when ended and one banner when archived, #6 VERDICT shown as a tag (summary text is stripped of it), #7 "Change ready" label for Build tasks, #9 44px touch targets on touch screens and a bordered "More", #10 presets show dollar values, #11 reason shown beside the disabled Create button, #12 "0" instead of "-0".
Changed but not separately reproducible: #3 (dialogs are native `<dialog>`, so the Tab "leak" was probably a test artifact; focus now explicitly returns to the opener).
Open: #6 summary truncation (came from the fake summarizer, check with a real model), #13 menu arrow-key navigation, #14 repository path check, Back/refresh mid-session.
