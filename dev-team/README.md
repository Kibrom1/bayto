# Bayto build team (dev-team)

A team of role seats (default: coordinator, product-owner, researcher, architect, backend-engineer, qa-tester), running in one Docker Sandbox, implements work-plan tasks in THIS repo
(mounted read-write). Chosen policy: engineer seats commit on `sbx/<task>` branches and, after QA passes, push the branch and open a PR from inside the sandbox
(the sandbox proxy injects the GitHub credential over HTTPS); you review and merge on GitHub. Agents never push `main`, force-push or merge.

No GitHub token is stored in the VM: the proxy adds it to HTTPS requests. Needs the host secret once: `sbx secret set github --sandbox bayto-dev -t "$(gh auth token)"`.
`origin` is usually an SSH URL, so agents push to the `https://github.com/<owner>/<repo>.git` form (see `AGENTS.md`).

## Run (on your Mac)

    cd ~/Desktop/workspace/AI/bayto
    dev-team/run.sh bayto-dev          # needs Beans installed once: see vendor/wad-sbx-workshop/README.md

In the sandbox shell:

    start-team
    crew ask "Task M1.5: run the workshop factory on acp instead of handoff (see docs/work-plan.md)."
    crew watch

`dev-team/roster.txt` picks the seats (one `<role id> [model]` per line, from the `roles/` catalog); `run.sh` turns it into `team.tsv`,
one brief per seat and `roster.json` via `build-roster.py` (needs `uv`), and `start-team`, `handoff`, `crew` and `crew-notify` follow that roster.
Project-wide rules for every seat live in `team-rules.md`. Use another roster with `ROSTER=path dev-team/run.sh bayto-dev`.
Tool permissions are enforced per seat (M1.9). `build-roster.py` writes `tools.json` and one flags file per seat (`tool-flags/<seat>`); `run.sh` patches the launcher to ship
`tool-flags/` into the sandbox factory dir and patches `start-team` to pass each Claude seat its flags (`--allowedTools`, `--disallowedTools`, `--permission-mode dontAsk`,
`--add-dir ~/work`). `start-team` refuses to start a seat with no flags file instead of launching it unrestricted. Under `dontAsk` anything not on a seat's allow list is refused, so
the allow list is a real restriction. Seats whose profile denies Bash (coordinator, product-owner, researcher, architect) get only `Bash(handoff:*)`, `Bash(crew:*)` and
`Bash(touch ~/work/factory/ready/*)`; a compound command, redirect or pipe on those is refused (so no `handoff read ... | jq`). Seats with full Bash (engineers, qa-tester,
code-reviewer) can still write files through the shell, so their Write/Edit denial is a guide, not a hard wall. Verified with the `claude` CLI against a scratch directory and
unit tests; not yet proven end to end in a live sandbox, since that needs the host's `sbx` CLI. An already-running sandbox needs a relaunch to pick this up.

Agents open the PR themselves. To push branches from the Mac instead (or for the ones the team did not push):

    scripts/push-sbx-branches.sh

Cleanup: `exit`, then `sbx env rm dev-team/.build/factory/sbxenv.yaml --env-arg name=bayto-dev`.

Rules the team follows live in `AGENTS.md` and `roles/`. `scripts/push-sbx-branches.sh` skips branches that touch `vendor/` or secret-looking files.
