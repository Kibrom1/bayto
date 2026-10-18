# Bayto (ባይቶ)

A multi-agent discussion platform: pose a task or topic, seat a panel of AI agents
(together in one local Docker Sandbox per task), and let them chat, collaborate and debate their way
to a concrete artifact. *Bayto* is the traditional Tigrigna village assembly where the
community gathers, debates an issue openly and decides together.

## Status (2026-10-18)

The MVP is built and merged: task board, session setup, a live room (with streaming turns), four modes
(open chat, debate, brainstorm and **Build**), a human seat, memory and usage metering. Build mode has had one
passing live run on a throwaway repository (see [docs/work-plan.md](docs/work-plan.md), item B9); the MVP exit
test ([docs/mvp-exit-test.md](docs/mvp-exit-test.md)) has not run yet. There is no authentication: run it on your
own machine only and do not expose its ports.

## What it does

You write a task, pick a mode and seat agents. Each task gets one local Docker Sandbox (`sbx`); the agents talk
through a shared message log, a moderator decides who speaks, and the session ends with an artifact:

| Mode | Result |
| --- | --- |
| Open chat, Debate, Brainstorm | A synthesis (and a minority report for debates) |
| Build | A change set: commits on a `bayto/build-<id8>` branch of your repository, with test evidence and a review, ready for you to merge. Bayto never pushes or merges for you |

Build runs a fixed team (architect, developer, tester, reviewer) in a pipeline with up to three fix cycles.
It switches your repository to the build branch (the folder stays on it afterwards; the change set shows the
command to switch back), and a guard puts back any change the agents make to the repository's `.git` settings
and hooks. Details: [docs/build-mode-design.md](docs/build-mode-design.md).

## Run it

Needs: Postgres 16 running, `uv`, Node 22, the `claude` CLI signed in (Bayto uses your Claude subscription, no
API key) and `sbx`.

```
scripts/bayto-up.sh          # migrates and seeds the database, starts the API (:8000) and the web app (:5173)
```

Logs go to `.bayto-run/`. Versions are pinned in `versions.env` (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Tests

```
cd orchestrator && BAYTO_REQUIRE_DB=1 python -m pytest -q     # needs Postgres at DATABASE_URL
cd agent-comms  && python -m pytest -q                         # install with: pip install -e ".[dev]"
cd web          && npx tsc -b && npm test
```

GitHub Actions (`.github/workflows/ci.yml`) runs all three on every pull request. Live checks that need `sbx` and
a real model are scripts in `docs/e2e/live/`: `run_live_e2e.sh` (the three discussion modes, through the browser)
and `live_build_e2e.py` (Build, through the API; `--scenario fixcycle` forces a failed first attempt).

## Documents

| File | What it covers |
| --- | --- |
| [docs/product-design.md](docs/product-design.md) | Product and system design: concepts, modes, agents, explorer, sandboxes, orchestration, UI, architecture, roadmap, decisions |
| [docs/agent-communication-protocol.md](docs/agent-communication-protocol.md) | Reusable agent-to-agent communication protocol (`acp/1`), team roles and modes |
| [docs/work-plan.md](docs/work-plan.md) | Task-level plan to start implementing (W0, M0–M2 in detail, M3–M6 deliverables) |
| [docs/decisions.md](docs/decisions.md) | Decision log with dates and rationale |
| [docs/build-mode-design.md](docs/build-mode-design.md) | Build mode: pipeline, roles, permissions, branch handling, change set |
| [docs/mvp-exit-test.md](docs/mvp-exit-test.md) | The manual end-to-end test that closes the MVP |
| [docs/analysis-2026-10-18.md](docs/analysis-2026-10-18.md) | Project analysis: findings, risks and recommended order |
| [orchestrator/README.md](orchestrator/README.md) | The orchestrator: API, configuration, sandbox provider, moderator |

## Research

| File | What it covers |
| --- | --- |
| [research/docker-sandboxes.md](research/docker-sandboxes.md) | Docker Sandboxes (sbx): isolation model, local sbx usage (cloud kept for reference only), limits, pricing |
| [research/sandbox-runtime-comparison.md](research/sandbox-runtime-comparison.md) | Firecracker vs gVisor vs hardened Docker containers vs Docker Sandboxes |
| [research/wad-sbx-workshop-analysis.md](research/wad-sbx-workshop-analysis.md) | Analysis of the wad-sbx-workshop reference repo and how it maps to Bayto |
| [research/turn-taking.md](research/turn-taking.md) | Moderator-picked vs self-selected speakers; the raise-hand model |
| [research/pricing.md](research/pricing.md) | Pricing model options and recommendation |
| [research/naming.md](research/naming.md) | Name exploration (English, Amharic, Tigrigna) and the choice of Bayto |

## Reference implementation

The sandbox team setup began from [shelajev/wad-sbx-workshop](https://github.com/shelajev/wad-sbx-workshop); how it maps to Bayto is in [research/wad-sbx-workshop-analysis.md](research/wad-sbx-workshop-analysis.md).
