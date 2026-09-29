# Bayto build team (dev-team)

A coordinator / developer / QA team, running in one Docker Sandbox, implements work-plan tasks in THIS repo
(mounted read-write). Chosen policy: agents commit on `sbx/<task>` branches; you push from the host; PRs merge on GitHub.

Why host-side push: no GitHub credential enters the VM. A proxy-injected token (like `kits/multi-provider`) is a later spike:
git over HTTPS needs a Basic auth header the kit's `inject.format` may not express, so verify before relying on it.

## Run (on your Mac)

    cd ~/Desktop/workspace/AI/bayto
    dev-team/run.sh bayto-dev          # needs Beans installed once: see vendor/wad-sbx-workshop/README.md

In the sandbox shell:

    start-team
    crew ask "Task M1.5: run the workshop factory on acp instead of handoff (see docs/work-plan.md)."
    crew watch

Then, on the Mac, review and push:

    scripts/push-sbx-branches.sh

Cleanup: `exit`, then `sbx env rm dev-team/.build/factory/sbxenv.yaml --env-arg name=bayto-dev`.

Rules the team follows live in `AGENTS.md` and `roles/`. The push script skips branches that touch `vendor/` or secret-looking files.
