# Bayto readiness log

Read this file first, every session. It is the memory between rounds: do not redo, undo or contradict an
entry below. The Technical Writer appends to it after every approved fix, in the same change as the fix.

## Standing instructions (production-readiness review, open ended, continuous)

Work in small rounds. Each round: take the single highest-risk **Open** item (or find one), fix it with a test,
prove it, get it reviewed, log it. Then the next round.

Review areas, in order of risk:
1. **Reliability**: swallowed failures, sessions stuck in `starting` or `cancelling`, missing timeouts on sandbox
   and `claude -p` calls, unbounded retries, races on the shared sandbox and repository, crash safety of file and DB writes.
2. **Security**: auth bypass, secrets in logs or error text, command injection (control panel, sandbox argv), path
   traversal, host and CORS checks, what each seat's tool permissions really allow.
3. **Data**: migrations up and down, foreign keys and deletes (deleting a task that has a sandbox row fails),
   idempotency of start, stop and resume.
4. **Cost control**: the dollar cap is checked before each turn, runaway loops, token accounting, local-model fallback.
5. **Operability**: readable logs with the real cause, health checks, start-up order, clear user-facing errors.
6. **Usability**: the Services page and the room: states, empty and error cases, keyboard and narrow screens.
7. **Tests**: missing edge cases, flaky tests, anything important that is untested.

Rules:
- One change per round. Smallest diff that fixes it. No drive-by refactors.
- Every fix has a test that fails before and passes after.
- Tester runs the new test alone, then the full suites, and reports the exit codes.
- Reviewer rejects untested changes and any change in behaviour other parts depend on that is not called out.
- Anything that cannot be fixed or tested here goes to **Open** below with severity, evidence and a suggested fix.
- Never touch secrets, `.env` files, git remotes or CI credentials. Do not push.
- When a round finds nothing above `low`, record that here and pick the next area.

Test commands the team may run: `cd agent-comms && uv run pytest`, `cd orchestrator && uv run pytest`,
`cd web && npx vitest run`.

## Fixed (newest first)

| Date | Issue | Severity | Change | Test | PR |
|---|---|---|---|---|---|
| 2026-10-30 | Atomic writes in `handoff_compat` and `crew_notify_fallback` did not fsync before rename | medium | flush + fsync before `os.replace` | `test_atomic_write_*_fsyncs_before_rename` | #233 |
| 2026-10-30 | Sandbox command failures hid the real cause (gRPC noise on stderr, synthetic error messages) | high | `failure_cause` ignores noise and reads the synthetic assistant text | `test_seat_cannot_run.py` | #229, #230 |
| 2026-10-30 | Services page: session Stop returned 422 (no body) | medium | panel sends `{"synthesize": false}` | `scripts/tests/test_control_start_all.py` | #232 |
| 2026-10-28 | Session start and resume returned 503 before 404 and 409 | medium | existence and state checks run before the login check | `test_login_check.py` | #224 |
| 2026-10-28 | A failed seat turn left the session `active` forever | high | two failures in a row pause the session with the reason | `test_seat_cannot_run.py` | #225 |

## Open

| Severity | Issue | Evidence | Suggested fix |
|---|---|---|---|
| high | A session cancelled before its first turn can stay in `cancelling` and blocks every new session of the task | Observed 2026-10-30: stuck until the orchestrator restarted | Time-box cancel; let a new session start once the old one is finished or stale |
| medium | Deleting a task that has a sandbox row fails with a foreign key error | `sandbox_task_id_fkey` violation in the orchestrator log | Delete or cascade the sandbox row, with a test |
| medium | Sandbox `claude` login expiry is only discovered on the first turn | Every seat exits 1 with "OAuth session expired" | Probe the login in the sandbox at start |
| low | The first sandbox creation can take minutes with no progress shown | "Starting" with no step detail | Show each start-up step and a timeout |

## Findings from research and design sessions

(Researcher, UX Designer, Security Reviewer, QA Tester and E2E Tester add dated notes here.)
