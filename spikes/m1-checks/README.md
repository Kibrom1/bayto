# M1.11–M1.16 host checks

Scripts for the open M1 measurement and security tasks in `docs/work-plan.md`. They run on the Mac (they need the real `sbx` CLI, which the dev-team sandbox does not have)
and write a markdown report per run to `results/` (git-ignored) for you to paste into the work plan. **Nothing here has been run yet**: it was written without access to `sbx`,
so expect to fix small things on the first run. Only `sbx exec [-i] [-w]`, `sbx env create/rm`, `sbx policy ls` and `sbx version` are used; they appear in the vendored launcher or the repo docs.
`sbx stop` is the one unverified command (M1.14); override it with `SBX_STOP_CMD="..."` or stop the sandbox by hand when the script asks.

| Task | Script | Needs | Spends tokens |
|---|---|---|---|
| M1.15 security | `m1-15-security.sh <sandbox> [--no-llm]` | a running dev-team sandbox **relaunched from the current `dev-team/run.sh`**, team started | ~1 Haiku call per seat unless `--no-llm` |
| M1.12 sizing | `m1-12-sizing.sh ["3 5 7"]` | Beans installed, no other sandbox named `bayto-m112-*` | ~15 short calls |
| M1.11 mount | `m1-11-mount.sh <sandbox> [N]` | any running dev-team sandbox (uses a throwaway `.m1-11-probe/` in the mounted repo) | none |
| M1.14 persistence | `m1-14-persistence.sh <sandbox>` | running sandbox, ideally with the team started; **it stops the sandbox** | none |
| M1.13 streaming | `m1-13-streaming.sh <sandbox>` | running sandbox; part 1 is more useful after a real task | one Haiku call |
| M1.16 scorer smoke | `cd orchestrator && uv run --extra dev python ../spikes/m1-checks/m1-16-scorer-smoke.py` | a real `ANTHROPIC_API_KEY` on the host (no sandbox) | 3 short Haiku calls |

Suggested order: relaunch, `start-team`, then M1.15 (answers the open GH_TOKEN and allowlist findings), M1.11, M1.13, M1.14 on the same sandbox, and M1.12 last because it creates and removes its own sandboxes.

    dev-team/run.sh bayto-dev                      # relaunch; then in its shell: start-team
    spikes/m1-checks/m1-15-security.sh bayto-dev
    spikes/m1-checks/m1-11-mount.sh bayto-dev
    spikes/m1-checks/m1-13-streaming.sh bayto-dev
    spikes/m1-checks/m1-14-persistence.sh bayto-dev
    spikes/m1-checks/m1-12-sizing.sh

Exit status is 0 only if there are no FAIL lines. INFO lines are things to read, not failures.

## Notes

- `dev-team/run.sh` gained two env switches for these scripts: `SESSION=none` (create and prepare the sandbox, do not attach a terminal) and `NO_SWITCH=1` (do not `git switch main` in your checkout).
- M1.15 never prints a secret: it reports names, lengths, a 4-character format prefix and whether `GH_TOKEN` equals your host `gh auth token` (compared by hash).
- M1.15 section D compares what each seat actually did with what its `tool-flags/<seat>` file says it may do. A seat with full Bash but denied Write (qa-tester, code-reviewer) is expected to show Write refused and Bash allowed.
- M1.12's MB/agent and recommended size are simple heuristics; the work plan item wants you to decide the agent cap from the table.
- M1.10 (the Python floor-loop driver) is not covered: M2's orchestrator supersedes it (docs/decisions.md).
