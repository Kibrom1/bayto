# Bayto test and evaluation: 2026-10-14

Scope: live UI testing and code review of commit `5bd8f94` (merge of PR #91), from a fresh clone.
Environment: Linux container, Python 3.13, Node 22, Postgres 16, headless Chromium (Playwright).

**Not covered:** the `sbx` CLI was not available, so no live multi-agent session ran. Real discussion flow,
streaming against live agents, the `claude -p` moderator path and sandbox provisioning are untested.

## Test suites

| Suite | Result |
| --- | --- |
| agent-comms | 162 passed, 1 xpassed |
| web (vitest) | 9 passed; `tsc -b` clean; oxlint: 2 warnings |
| orchestrator, no Postgres | 123 passed, 173 skipped (skips are silent, so coverage is overstated) |
| orchestrator, live Postgres | 294 passed, 2 failed |

Failures:
- `test_provision.py::test_create_uses_the_provisioner_instead_of_env_create`: `from tests.test_local_sandbox import ...`
  fails under plain `pytest` (no `tests/__init__.py`); passes with `python -m pytest`.
- `test_api_sessions.py::test_sse_emits_rolling_summary_events`: fails 5 of 5 runs. Points at finding 1 below.

## Findings, most serious first

1. **SSE loses events** (`orchestrator/api/sessions.py` `session_events`, `pubsub.py`).
   - `PubSub` keeps no history. A client connecting after the runner finishes gets an empty stream
     (`runtime is None or runner_task.done()` returns immediately).
   - With `since_seq`, messages published between the backlog query and `subscribe()` can be missed.
   - `rolling_summary` and `turn_*` events are never replayed on connect or reconnect.
   - Subscriber queues are unbounded, so a slow client grows memory.
   - Status: first two confirmed by code reading and the failing test; the race itself was not reproduced.
2. **Missing input validation** (`api/tasks.py`, `api/sessions.py`).
   - NUL byte in a task title: 500. Same agent twice in a roster: 500 (`session_agent_pkey` violation).
   - Empty title, brief and output_type accepted (201). A 5 MB title accepted. Empty roster accepted.
3. **First run fails without undocumented config.** `POST /sessions/{id}/start` returns 500 until
   `BAYTO_SUMMARY_MODEL`, `BAYTO_SYNTHESIS_MODEL` and `BAYTO_HAND_RAISE_MODEL` are set. They appear in no README.
4. **Failure details leak to the client.** A failed session returns raw `run.sh` output including absolute host
   paths. The API has no authentication (documented, deferred to M5), which raises the impact.

## UI

Works: create-task form with client-side validation, session setup, native `<dialog>` with Esc to close, skip link.

Problems:
- Long titles break the layout (a 5 MB title gave mobile scroll width of about 33M px). No truncation or wrapping.
- An empty-title task renders as a blank card.
- Unknown routes (`/nope`) render a blank page; there is no 404 route.
- A bad session ID shows the raw FastAPI 422 JSON in the room view.
- A failed session's room still shows enabled Pause and End & synthesize buttons.
- `/setup/<nonexistent id>` renders a full setup form.

## Smaller items

- Moderator passes the whole prompt as a `claude -p` argument; long transcripts can hit the OS per-argument limit.
- Moderator JSON reply is parsed but not validated against the tool's input schema.
- Stray files: `agent-comms/uv 2.lock`, `orchestrator/tests/test_api_pause_resume 2.py`.
- Lint warnings: `web/src/pages/Services.tsx:83` (set-state-in-effect), `web/src/pages/Synthesis.tsx:33` (fast-refresh export).

## What held up

- 6 concurrent `/start` calls gave one 202 and five 409s, so the status transition is guarded in practice.
- `scripts/bayto-control.py` binds to 127.0.0.1, checks the Host header and requires a custom header on POST.
- Migrations apply and roll back cleanly; mirror and floor logic are well tested.

## Suggested order of fixes

1. Server-side validation (finding 2): length limits, non-empty fields, NUL stripping, roster uniqueness.
2. SSE replay and subscribe-before-backlog ordering (finding 1), with bounded queues.
3. Document the model env vars and add a startup check with a clear error (finding 3).
4. UI: 404 route, text overflow handling, friendly error for bad IDs, disable controls on terminal sessions.
5. Add `tests/__init__.py` or a pytest `pythonpath` setting; make the Postgres skip loud in CI.
