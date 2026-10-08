# Plan: hold the team's `sbx exec` open (orchestrator `start_team`)

Status: implementing on `sbx/m5-held-team`. Written 2026-10-08.

## Problem

Findings from M1.12 and the M1.15 rerun (docs/decisions.md 2026-10-06, docs/work-plan.md):

1. Processes started by `sbx exec` die when that exec returns. `LocalSbxSandboxProvider.start_team`
   runs `sbx exec <name> start-team` once and waits for it to return, so the herdr server and every
   seat die immediately after.
2. A stale `~/work/factory/team-started` marker (left by an earlier team) makes `start-team` exit at
   once and start nothing.
3. Starting is slow: about 55 s per agent (98 s for 3 agents, 244 s for 5, 437 s for 8).
4. `restart_team` exists (clears the marker, runs `start-team`) but is a one-shot exec too, so it has
   problem 1, and nothing calls it.

## Design

- `SbxRunner` gets a second method, `start(argv)`, that launches a long-lived `sbx exec` and returns a
  handle with `poll()` and `terminate()`. `SubprocessSbxRunner.start` uses `subprocess.Popen` with
  output going to a log file under the temp directory.
- `LocalSbxSandboxProvider.start_team` starts
  `bash -lc 'rm -f $HOME/work/factory/team-started; start-team; sleep infinity'` as a held exec and keeps
  the handle in `self._held[sandbox.name]`. Removing the marker first fixes problem 2.
- It then polls `test -f $HOME/work/factory/team-started` through a normal `sbx exec` until the file
  appears. Defaults: 5 s interval, 600 s timeout (8 agents took 437 s). If the holder exits early or the
  timeout passes, the handle is released and a `SandboxProviderError` is raised with the log tail.
- `start_team` is a no-op if a live holder already exists for that sandbox (idempotent).
- `restart_team` releases any old holder, then starts a new one (the sandbox was stopped, so the old
  holder is dead anyway).
- `stop`, `remove` and a new `close()` release holders first. Releasing a holder prints
  `error: inspect exec: context deadline exceeded`; that is harmless (M1.12).
- `launch_runner` is unchanged: it already runs as a background task while the session is `starting`.

## Known limits (stated, not fixed here)

- The holder is a child of the orchestrator process. If the orchestrator exits, the team dies. After a
  restart the orchestrator must call `restart_team` for sessions that were active; wiring that into
  startup/`reconcile` is a follow-up.
- Timing comes from M1.12 on one machine. The 600 s timeout is a guess, not a measured ceiling.
- Not yet verified against a live `sbx`: the new code is tested with a fake runner only. Live check: `spikes/m1-checks/m5-held-team-live.sh bayto-dev` (sandbox running, no team started).

## Tests (fake runner, no `sbx` needed)

holds the exec and waits for the marker; raises when the holder exits early; raises on timeout and
releases the handle; second `start_team` is a no-op; `restart_team` releases the old holder; `stop` and
`close` release holders.

## Out of scope

Reduced start time (sequential startup and acknowledgment waits), the CPU question from M1.12, and the
MVP exit test itself.
