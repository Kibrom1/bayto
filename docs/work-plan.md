# Bayto work plan

2026-09-28 · Owner: Kibrom · Status: ready to start

This plan turns the roadmap (M0–M6 in [product-design.md](product-design.md)) into concrete tasks. M0–M2 are broken down to the task level; M3–M6 stay at deliverable level and get detailed when M2 lands. Estimates assume one developer working part-time (about 15–20 hours a week) and are rough.

## Timeline

| Milestone | Goal | Rough effort | Depends on |
| --- | --- | --- | --- |
| W0 | Setup: tools, repo scaffold, credentials | 2–3 days | — |
| M0 | Debate spike in one sandbox (workshop reused) | 1 week | W0 |
| M1 | Team sandbox + `acp` protocol (File transport) | 2–3 weeks | M0 |
| M2 | Python (FastAPI) orchestrator + message mirror to Postgres + SSE | 2–3 weeks | M1 |
| M3 | Bayto room UI | 3 weeks | M2 |
| M4 | Bayto MCP server via SBX gateway | 1–2 weeks | M2 |
| M5 | Human seat + task-scoped memory | 2 weeks | M3, M4 |
| M6 | Hardening + usage metering (local) | 2–3 weeks | M5 |

MVP exit: 5 real tasks run end to end, artifacts kept, judged better than single-agent answers.

## W0 — Setup

- [x] **W0.1** Install and pin tools on the Mac: `sbx` (`brew install docker/tap/sbx`), Claude Code CLI, Python 3.12 + `uv`, Node LTS (for the Claude CLI), Postgres (or run it inside a sandbox). Record versions in `versions.env`.
- [x] **W0.2** Claude auth through the SBX proxy (subscription `/login` or proxy-managed API key, as in the workshop's `kits/multi-provider`); never put a real or dummy key in the repo. Confirmed signed in to sbx and Claude.
- [x] **W0.3** Verify the workshop still runs end to end locally (chapter 4 team with `claude-haiku`).
- [x] **W0.4** Scaffold the repo layout from product-design.md (`kits/`, `envs/`, `roles/`, `modes/`, `agents/templates/`, `agent-comms/`, `orchestrator/`, `web/`, `spikes/`) with a README in each.
- [ ] **W0.5** Add `.gitignore`, license choice, and a `CONTRIBUTING`-style note on version pinning.

**Done when:** workshop chapter 4 runs on your Mac and the empty Bayto scaffold is in place.

## M0 — Debate spike in one sandbox

Reuse the workshop almost unchanged to prove a Claude debate works inside SBX and to learn where it breaks.

- [x] **M0.1** Copy the chapter 4 configuration into `spikes/m0-debate/` (sbxenv, `team.tsv`, `chapter.env`, `PROMPT.md`).
- [x] **M0.2** Write role briefs: `moderator.md`, `advocate.md`, `skeptic.md`, keeping the workshop rules (read with `handoff read`, send with `crew send`, end the turn after sending, never poll).
- [x] **M0.3** Map roles onto the workshop's fixed role names (coordinator = moderator, developer = advocate, qa = skeptic), since `handoff` only accepts those.
- [x] **M0.4** `PROMPT.md`: one debate topic plus the rules (3 rounds, then moderator summary to `human`).
- [ ] **M0.5** Run three topics; save transcripts (`messages/*.json`) and the summary under `spikes/m0-debate/runs/`.
- [ ] **M0.6** Write findings: token use per round, failure modes (echo, dominance, stalls), wake-up reliability.

**Done when:** `crew watch` shows a 3-round debate and a moderator summary for 3 topics, and findings are written.

## M1 — Team sandbox + `acp` protocol

Keep the workshop topology (one sandbox, a Herdr session per agent) but replace its hard-coded roles and `handoff` with a dynamic roster and the `acp/1` protocol.

**Protocol (`agent-comms/`)**

- [ ] **M1.1** `schema/message.v1.json`, `report.v1.json`, `hand-raise.v1.json` from the protocol doc.
- [x] **M1.2** `acp` CLI: send, read, ack, transcript, report, claim, stage (dynamic roles via `--roles`)
- [x] **M1.3** `FileTransport`, backward compatible with the workshop's `$FACTORY_DIR` layout.
- [x] **M1.4** Conformance tests (port `scripts/tests/crew.py` and `crew-notify.sh`): concurrent `seq`, crash-before-consume redelivery, broadcast consumption, stale attempts, single-fire claims, busy wake-up returns 3, permission rejection.
- [ ] **M1.5** Compatibility check: run the workshop factory on `acp` instead of `handoff` with no behavior change.

**Team sandbox**

- [ ] **M1.6** `kits/bayto-team/spec.yaml` (patterns: `chapters/kits/pi`, `chapters/kits/herdr`, `chapters/kits/multi-provider`): installs Claude CLI, Herdr and the `acp` CLI; network allowlist = Anthropic API only for now; credentials proxy-managed.
- [ ] **M1.7** `envs/team.sbxenv.yaml` template (name arg, cpus, memory, kits, workspace).
- [ ] **M1.8** Dynamic roster: generate `team.tsv` (role, harness, provider, model) and per-role briefs from a session roster and the role catalog; adapt the workshop's `start-team` to it, keeping the readiness acks.
- [ ] **M1.9** Per-role tool permissions: pass allow/deny lists to each Claude session (for example a Researcher without file writes).
- [ ] **M1.10** `spikes/m1-driver/`: a small Python driver that creates the sandbox with `sbx env create`, starts the team, then runs the floor loop: raise-hand signals from one Haiku call, `assignment` message, `crew-notify`, tail the messages, repeat until convergence, then a moderator summary; removes the sandbox with `sbx env rm`.
- [ ] **M1.11** Test a read-write host mount for the factory directory (visibility, atomic renames, no NULL-filled files); if it fails, fall back to a sandbox-local factory directory read with `sbx exec`.
- [ ] **M1.12** Measure sandbox memory and CPU with 3, 5 and 7 agents, sandbox creation time, and assignment-to-first-message latency. Record the recommended sandbox size and agent cap.
- [ ] **M1.13** Check streaming: is message-level output enough for the Bayto room? If not, prototype the headless turn runner (`claude -p --output-format stream-json` through `sbx exec` in the same sandbox).
- [ ] **M1.14** Check that agent state survives a sandbox stop and start (needed for task-scoped memory).
- [ ] **M1.15** Security check: agents can't read the real API key, the network allowlist blocks other domains, and role tool permissions actually deny what they should.

**Done when:** the M0 debate runs with roles generated from a roster, the conformance suite passes on `FileTransport`, and the M1.12–M1.14 measurements are written up.

## M2 — Orchestrator service

- [ ] **M2.1** Python project `orchestrator/` managed with `uv`: FastAPI, Pydantic v2, SQLAlchemy 2 (async) + psycopg 3, Alembic migrations, `sse-starlette`, pytest + pytest-asyncio; reuse the `acp` package from M1.
- [ ] **M2.2** Schema: product tables (`task`, `agent`, `mode`, `session`, `session_agent`, `sandbox`, `turn`, `artifact`) plus `message` and `report` mirror tables.
- [ ] **M2.3** Message mirror: a file watcher on the factory directory reads new `acp` messages and reports, writes them to Postgres in `seq` order, and publishes them to SSE subscribers. It resumes from the last mirrored `seq`.
- [ ] **M2.4** `SandboxProvider` interface + `LocalSbxSandboxProvider`: create the team sandbox for a task, start the team, wake a role, stop, remove, and reconcile with `sbx ls` at startup.
- [ ] **M2.5** Conversation core: envelope validation, send permissions from mode files, stage state machine with claims.
- [ ] **M2.6** FloorPolicies: `round-robin` and `raise-hand` (one Haiku call scoring all personas).
- [ ] **M2.7** Moderator agent: opening, rolling summary, stop conditions (rounds, budget, convergence), final synthesis into an `artifact`.
- [ ] **M2.8** REST API: create task, create session (roster + mode), start, interject, stop; SSE stream of messages.
- [ ] **M2.9** Resilience: restart the orchestrator mid-session; it re-attaches to the running team sandbox and resumes from the last `seq`.
- [ ] **M2.10** Budgets: token/cost cap per session; OpenTelemetry spans per turn (tokens, latency, cost).
- [ ] **M2.11** Seed data: 6 agent templates, the 7 team roles, modes `open-chat`, `debate`, `brainstorm`.

**Done when:** a REST call starts a debate in a real team sandbox, messages stream over SSE, and the session survives an orchestrator restart.

## M3–M6 (deliverable level)

| Milestone | Deliverables |
| --- | --- |
| M3 Bayto room UI | Task board; session setup with Agent explorer over templates; live streaming transcript; interject / @mention; end-and-synthesize; cost meter |
| M4 Bayto MCP server | `mcp/bayto-mcp` with `get_task_context`, `request_human`, `cite_source`; attached through the SBX MCP gateway; per-participant identity |
| M5 Human seat + memory | `decision-request` flow in the UI; pause/stop the team sandbox while waiting; team sandbox kept for the task so each role resumes its own Claude session; second session recalls the first |
| M6 Hardening + usage metering | Team sandbox sizing and agent cap from M1 measurements; cleanup of orphaned sandboxes on restart (`sbx ls` reconcile); token budgets per session; usage ledger and cost report per session |

## Working agreements

- Tick a task (`- [x]`) in this file in the same commit that completes it, and only when its "done" check really passed (tests run, spike run, or file delivered). Partly done tasks stay unticked with a note.
- Pin every external version (sbx, Claude CLI, kits by commit), as the workshop does.
- Every spike ends with a short findings note in `spikes/<name>/FINDINGS.md`.
- Keep product decisions in [decisions.md](decisions.md).
- Keep secrets out of the repo; use SBX-managed credentials only.

## Decisions needed before or during M1

- [ ] Confirm raise-hand as the default floor policy (currently Proposed).
- [ ] Confirm the `acp/1` protocol direction (currently Proposed).
- [x] Topology: all agents share one team sandbox per task for now (decided 2026-09-28); per-agent isolation is a later option.
- [x] Language for `acp` and the orchestrator: Python (decided 2026-09-28), one shared package.
- [ ] Pricing/billing is out of scope while Bayto runs locally only; revisit if it becomes a hosted product.
