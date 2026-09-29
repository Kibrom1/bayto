# Working on Bayto (for agents)

Read `docs/work-plan.md` (tasks), `docs/product-design.md` and `docs/agent-communication-protocol.md` before coding.

- Branch: work only on a branch named `sbx/<short-task-name>`. Never commit to `main`. Never push; a human pushes from the host.
- Python 3.12, `uv`, pydantic v2. The protocol package is `agent-comms/` (run tests: `cd agent-comms && uv venv --python 3.12 && uv pip install -e ".[dev]" && .venv/bin/pytest -q`).
- Do not edit `vendor/` (copied third-party code), `docs/decisions.md` history, or anything containing credentials. Never write secrets into the repo.
- Small commits, message = what and why. End each message with the trailer line: `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Tick finished items in `docs/work-plan.md` in the same commit as the work. Record new decisions in `docs/decisions.md`.
- A task is done only when its tests pass with real exit codes. Report failures as failures.
