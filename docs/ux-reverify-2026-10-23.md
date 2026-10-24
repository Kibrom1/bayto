# Bayto UX re-verification, 2026-10-23 (branch fix/e2e-round-4)

Method: real app with fakes (docs/e2e/features/README.md), fresh DB bayto_e2e, second instance with BAYTO_API_TOKEN for sign-in. Playwright Chromium, 1280 and 390, light and dark, axe-core on every page (1280 light). Screenshots in /tmp/ux-review-2/ (`<page>-<width>-<scheme>.png`). States re-created: finished, running, paused, interrupted (orphaned), failed, Build with change set, attachments with roles (including a duplicate name). Product code unchanged.

Totals: Fixed 23, Partly 8, Not fixed 0, Regressed 0 (31 findings).

## Per finding
| # | Status | Evidence |
|---|---|---|
| 01 | Fixed | room-running-390-light: actions wrap, no horizontal scroll on any room at 390 |
| 02 | Fixed | room-finished-1280-light: opens on Final synthesis |
| 03 | Fixed | room-failed-1280-light: cause in banner, primary "Retry with same team", "No turns were recorded." |
| 04 | Fixed | room-failed-1280-light: one-line "Session ended. [Start a new session] [Export]" bar, no disabled form |
| 05 | Fixed | live-sheet-context-390-dark: Team / Context (badge 3) / Usage bottom sheet from "Team & context" |
| 06 | Fixed | live-kebab-390-light, live-remove-confirm-1280-light: row menu, Remove confirm, Add agent in live/paused only |
| 07 | Fixed | room-export-390-light: one Export menu (Decision record, Full transcript, Copy synthesis) |
| 08 | Partly | room-finished-1280-light neutral banner OK; room-build-1280-light still neutral grey, text still "because build complete", not green |
| 09 | Fixed | room-running-1280-light: one status chip, breadcrumb "Tasks / <title>", "Spent $ of $ cap" |
| 10 | Fixed | live-rail-context-1280-light: list grows, "Visible to" chip, lock, duplicate-name warning |
| 11 | Partly | Context label regular weight; but newtask-build-1280-light still shows the semi-bold "Find passages by meaning" switch in New task. "Not set up" state not testable (provider configured) |
| 12 | Partly | setup-nosession-1280-light: title/brief, Debate preselected, Start disabled with reason; Open Chat card still says "Raise Hand" |
| 13 | Fixed | setup-sticky-390-light: sticky "3 seats, about $1.50 / $5 cap [Start]", roster above catalogue, filter |
| 14 | Fixed | setup-warn-1280-light: full names, model select under name |
| 15 | Fixed | setup-warn-1280-light: "Use suggested $2", preset unhighlighted at $0.20; Start stays active |
| 16 | Fixed | newtask-390-light: pinned footer, output chips. Build inline reveal not exercised |
| 17 | Fixed | board-1280-light, board-search-empty-1280-light: "Filter by title", names query, Clear search |
| 18 | Partly | board-390-light: kebab, state-matched actions (Resume/Retry/Open room/Start session); status "Done" vs filter "Finished", no Paused/Failed/Interrupted filter |
| 19 | Partly | board, services, landing, 404, setup titled "<page> · Bayto"; rooms still "Room · Bayto" (not task title) |
| 20 | Fixed | room-missing-1280-light: NotFound-style heading, h1, Back button |
| 21 | Fixed | signin-390-light, signin-wrong-390-light: heading, helper, Show token, "That token was not accepted" |
| 22 | Fixed | services-1280-light: "Developer tools", explanation, Retry |
| 23 | Fixed | live-rail-context: aria-label begins "Visible to: ..." matching visible text; axe label-content-name-mismatch gone |
| 24 | Partly | sheet controls 44px at 390; sheet Close 34px; desktop rail chips 23px, buttons 25-28px |
| 25 | Fixed | landing-lazy-1280-light: images load; grey skeleton while loading |
| 26 | Partly | pill anchored at composer edge with "N new turns", but still overlaps the last line (room-finished-1280-light) and shows "16 new turns" on a finished session at open |
| 27 | Partly | room-failed-1280-light, room-interrupted-v-1280-light: neutral surface now, but filler "The summary appears after the first turns." still shown |
| 28 | Fixed | room-build-1280-light: "Open pull request..." in card header, switch-back in details |
| 29 | Fixed | setup-agent-dialog-390-light: "Custom agents join discussions only; they cannot be Build seats." |
| 30 | Fixed | board-390-dark, room-finished-1280-dark: light neutral primary, amber only for warnings (see N3) |
| 31 | Fixed | board-1280-light: green branch "Change ready" chip, sorted first |

## axe (1280 light unless noted)
Clean: landing, board, search, services, 404, missing room/setup, sign-in, setup (all), finished, failed, interrupted. No contrast violations anywhere.
Found: aria-valid-attr-value (critical) on `.rail-toggle` at 390 (room-sheet-running); scrollable-region-focusable (serious) on `aside` in the Build room; landmark-unique (moderate) on `#rail-panel-context` in paused/running/live rooms.

## New issues introduced
- N1 P2: `.rail-toggle` ("Team & context") has an aria-controls value that points at no element while the sheet is closed (axe critical).
- N2 P2: Build room rail `aside` scrolls but is not keyboard focusable (axe serious).
- N3 P2: "Continue as Build" is a filled amber button in the finished header (room-finished-1280-light/dark), so amber is again a primary-looking colour, against the UX-30 rule.
- N4 P3: Duplicate `#rail-panel-context` landmark (axe moderate).
- N5 P3: At 390 the top nav wraps "Developer tools" and "Send feedback" onto two lines on every page (board-390-light).
- N6 P3: "N new turns" pill on a finished session at first view counts transcript below the fold, which is not new.
- N7 P3: Sheet Close 34px at 390; audience chips 23px at 1280.
- N8 P3: Roster meta ("scope-discipline - claude-sonnet-5 - 1 turn") wraps mid-phrase in the 1280 rail.
- Functional (not UX): DELETE of a needs_human session that has messages hung and logged a message_session_id_fkey FK violation; deleting the whole task worked.
