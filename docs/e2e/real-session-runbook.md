# Real-session runbook (Mac)

What the fake-model harness could not reach: a real `sbx` team, the real `claude -p` moderator, and raise-hand
with real models. Run this once after any change to the runner, modes, sandbox provisioning or the room UI.
Everything below is a check with an expected result; write down anything that differs. The commands
are taken from `orchestrator/README.md` and `spikes/m1-checks/README.md`; none of this runbook has been run yet,
so fix small things on the first pass.

## Scripted version

Steps 2 to 4 are automated in [`live/`](live/): `live_e2e.py` (Playwright, API as ground truth) and
`run_live_e2e.sh` (starts the stack, runs both modes, collects logs).

```sh
BAYTO_REPO=$PWD docs/e2e/live/run_live_e2e.sh                 # debate, then open-chat
BAYTO_REPO=$PWD docs/e2e/live/run_live_e2e.sh debate --headed
```

The scripts only talk to the running app over HTTP and import nothing from the repo, so they remain an
independent tester even though they live here. Results go to `docs/e2e/live/runs/` (git-ignored). Verified only
against the fake-model harness; the sandbox, `claude -p` and startup parts are untested.

## 0. Setup

```sh
# Postgres up, then:
cd orchestrator
uv run alembic upgrade head && uv run python -m orchestrator.seed
export BAYTO_SUMMARY_MODEL=claude-sonnet-5-5 BAYTO_SYNTHESIS_MODEL=claude-sonnet-5-5 \
       BAYTO_HAND_RAISE_MODEL=claude-sonnet-5-5 BAYTO_LLM_BACKEND=cli BAYTO_STREAMING_TURNS=1
uv run uvicorn orchestrator.app:app --port 8000      # terminal 1
cd ../web && npm run dev                              # terminal 2  (http://localhost:5173)
```

Expected: no "not configured" warning at API startup; `GET /healthz` returns `{"status":"ok"}`.
Keep `ANTHROPIC_API_KEY` unset (Bayto is subscription-only).

## 1. Moderator through your Claude login (no sandbox, ~2 calls)

```sh
spikes/m1-checks/moderator-cli-live.sh
```
Expected: a summary and a synthesis come back. Then confirm schema checking is live: the same run must not
fail with a `KeyError` or "expected string" anywhere downstream. (A reply of the wrong shape should show up as
`ClaudeCliError: reply does not match the schema ... (after retry)`, never as a crash inside the runner.)

## 2. Debate, three seats, to the end (round-robin)

UI: New task → pick **Debate** → add 3 agents → Start session.
Check, in order:
- [ ] Room opens; status goes `starting` → `active`. The start stage text changes while sandboxes build.
- [ ] Turns arrive one speaker at a time, with a live bubble before each finished message.
- [ ] Transcript shows only conversation. **No** assignment prompts, hand-raise or `x-*` JSON.
- [ ] Moderator summary updates; usage meter moves.
- [ ] Session ends by itself at `max_rounds` (6) and the synthesis appears in the room.
Failure to look for: status stuck on `active` with no turns (the old mode-permission crash), or
`failed` with a reason that now names what broke.

## 3. Pause, Resume, Stop on a real team (the untried exit-test item)

During a running debate:
- [ ] Pause. Status becomes `needs_human`, no new turns are granted after the turn in flight.
- [ ] Wait 1 minute, Resume. A new turn starts within one turn's time (not minutes).
  Seat conversations are not resumed (known, unverified): note whether the seats still make sense.
- [ ] Reload the browser mid-session: the whole transcript comes back, no duplicates.
- [ ] End & synthesize. Status goes `stopping` → `finished` in about a minute, and the synthesis renders.
- [ ] After it ends, Pause/Resume/Stop are disabled and the browser makes no further `/events` requests.
Failure to look for: Resume that goes quiet for the 300 s grant timeout (the old double-mirror bug),
or `stopping` that never finishes.

Also stop and resume the *sandbox itself* once: `spikes/m1-checks/m1-14-persistence.sh <sandbox>` (it stops the
sandbox), then Resume the session. Expected: the `team-started` marker is cleared and seats re-register.

## 4. Raise-hand mode with real models

UI: New task → **Open chat** (raise-hand floor, no max_rounds) → 3 agents → Start.
- [ ] Each round the moderator scores hands and a speaker is chosen; the choice varies (not always seat 1).
- [ ] An agent with nothing to add stays quiet instead of repeating.
- [ ] Interject as the human: the next turns respond to your note.
- [ ] No `ScorerError` in the API log (`no 'tool_use' block` means the scorer reply was the wrong shape).
- [ ] End & synthesize still produces a synthesis, since there is no round limit.

## 5. Sandbox and security checks that were never in a session

```sh
dev-team/run.sh bayto-dev          # then in its shell: start-team
spikes/m1-checks/m1-15-security.sh bayto-dev
spikes/m1-checks/m1-13-streaming.sh bayto-dev
spikes/m1-checks/m5-turn-live.sh
```
Expected: 0 FAIL lines. `m5-turn-live.sh` passed on 2026-10-08.

## 6. What to send back

For anything that failed: the room's status and failure text, the last 100 lines of the API log, and
`orchestrator/.sessions/<session id>/` (the raw message files). Those are enough to reproduce.
Record passes in `docs/mvp-exit-test.md`: resume after a sandbox stop, streaming through the UI with several seats,
and the token budget against a real run are still open there.
