# Bayto work plan

2026-09-28 · Owner: Kibrom · Status: ready to start

This plan turns the roadmap (M0–M6 in [product-design.md](product-design.md)) into concrete tasks. M0–M2 are broken down to the task level; M3–M6 stay at deliverable level and get detailed when M2 lands. Estimates assume one developer working part-time (about 15–20 hours a week) and are rough.

**Next up:** M2.9 — Resilience, then the rest of M2.

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

- [x] **M1.1** `schema/message.v1.json`, `report.v1.json`, `hand-raise.v1.json` from the protocol doc.
- [x] **M1.2** `acp` CLI: send, read, ack, transcript, report, claim, stage (dynamic roles via `--roles`)
- [x] **M1.3** `FileTransport`, backward compatible with the workshop's `$FACTORY_DIR` layout.
- [x] **M1.4** Conformance tests (port `scripts/tests/crew.py` and `crew-notify.sh`): concurrent `seq`, crash-before-consume redelivery, broadcast consumption, stale attempts, single-fire claims, busy wake-up returns 3, permission rejection.
- [x] **M1.5** Compatibility check: run the workshop factory on `acp` instead of `handoff` with no behavior change.
- [x] **M1.5a** Fix `agent-comms/docs/m1.5-compat-check.md`: it says the bash reference's message-id suffix is "10 chars"; QA traced it to 6 random chars + the shell PID (variable length), because `set -o pipefail` makes the `|| printf "$$"` fallback fire after `head -c 6`. Done when the doc states that and the claim is re-checked against the script.

**Team sandbox**

- [x] **M1.6** `kits/bayto-team/spec.yaml` (patterns: `chapters/kits/pi`, `chapters/kits/herdr`, `chapters/kits/multi-provider`): installs Claude CLI, Herdr and the `acp` CLI; network allowlist = Anthropic API only for now; credentials proxy-managed.
- [x] **M1.7** `envs/team.sbxenv.yaml` template (name arg, cpus, memory, kits, workspace).
- [x] **M1.8** Dynamic roster: generate `team.tsv` (role, harness, provider, model) and per-role briefs from a session roster and the role catalog (`acp.roster` + `roles/` catalog); the workshop's `start-team` now reads its readiness-ack role list from `team.tsv` instead of a hard-coded `coordinator developer qa`, waits per role with a bounded timeout, resends the brief once in case the startup prompt was dropped, and reports a role as blocked (with the `herdr` command to inspect it) if it's stuck on Claude's one-time "read outside working directories" permission prompt, instead of only timing out. `dev-team/run.sh` now builds the team from `dev-team/roster.txt` (default: coordinator, product-owner, researcher, architect, backend-engineer, qa-tester) instead of the fixed trio: `start-team` writes `roster.json` from `team.tsv`, and `handoff`, `crew` and `crew-notify` read seats from it (default trio when absent).
- [x] **M1.9** Per-role tool permissions: pass allow/deny lists to each Claude session (for example a Researcher without file writes).
- [ ] **M1.10** `spikes/m1-driver/`: a small Python driver that creates the sandbox with `sbx env create`, starts the team, then runs the floor loop: raise-hand signals from one Haiku call, `assignment` message, `crew-notify`, tail the messages, repeat until convergence, then a moderator summary; removes the sandbox with `sbx env rm`.
- [ ] **M1.11** Test a read-write host mount for the factory directory (visibility, atomic renames, no NULL-filled files); if it fails, fall back to a sandbox-local factory directory read with `sbx exec`.
- [ ] **M1.12** Measure sandbox memory and CPU with 3, 5 and 7 agents, sandbox creation time, and assignment-to-first-message latency. Record the recommended sandbox size and agent cap.
- [ ] **M1.13** Check streaming: is message-level output enough for the Bayto room? If not, prototype the headless turn runner (`claude -p --output-format stream-json` through `sbx exec` in the same sandbox).
- [ ] **M1.14** Check that agent state survives a sandbox stop and start (needed for task-scoped memory).
- [ ] **M1.15** Security check: agents can't read the real API key, the network allowlist blocks other domains, and role tool permissions actually deny what they should.

**M1.10–M1.15 are skipped for now (decided 2026-09-29):** each needs the host's `sbx` CLI (`sbx env create`/`sbx env rm`, `sbx ls`) to actually create/measure/tear down a
team sandbox, which is not available inside this dev-team sandbox. They stay unchecked until run from a host with `sbx`. M2 does not depend on them landing first.

**Done when:** the M0 debate runs with roles generated from a roster, the conformance suite passes on `FileTransport`, and the M1.12–M1.14 measurements are written up.

## M2 — Orchestrator service

- [x] **M2.1** Python project `orchestrator/` managed with `uv`: FastAPI, Pydantic v2, SQLAlchemy 2 (async) + psycopg 3, Alembic migrations, `sse-starlette`, pytest + pytest-asyncio; reuse the `acp` package from M1. Minimal scaffold (human-scoped): project files, a `/healthz` endpoint, one passing test, a `uv.lock`; schema/migrations/mirror/API surface land in M2.2+.
- [x] **M2.2** Schema: product tables (`task`, `agent`, `mode`, `session`, `session_agent`, `sandbox`, `turn`, `artifact`) plus `message` and `report` mirror tables. `orchestrator/src/orchestrator/models.py` (SQLAlchemy 2 async) + one Alembic migration, verified against a real throwaway Postgres container.
- [x] **M2.3** Message mirror: a file watcher on the factory directory reads new `acp` messages and reports, writes them to Postgres in `seq` order, and publishes them to SSE subscribers. It resumes from the last mirrored `seq`. `orchestrator/src/orchestrator/mirror.py` (`MessageMirror`) + `pubsub.py`, verified against a real throwaway Postgres container (ordering, resume-after-restart, idempotency).
- [x] **M2.4** `SandboxProvider` interface + `LocalSbxSandboxProvider`: create the team sandbox for a task, start the team, wake a role, stop, remove, and reconcile with `sbx ls` at startup. `orchestrator/src/orchestrator/sandbox/{provider.py,local.py}`, verified against a real throwaway Postgres container (DB read/write per method, idempotent create, no-op-safe stop, reconcile drift); the `sbx` CLI argv beyond `env create`/`crew-notify` is unverified (no real `sbx` binary in this dev-team sandbox, same as M1.10-M1.15) -- see module docstring and docs/decisions.md.
- [x] **M2.5** Conversation core: envelope validation, send permissions from mode files, stage state machine with claims. Includes the deferred `visibility` enforcement from M1.1b: `recipients` and `moderator` are accepted/stored on the envelope today but nothing actually hides a `recipients`- or `moderator`-visibility message from anyone but its intended reader (only `all` is used by `Conversation.transcript()`). Reused `agent-comms`' `Conversation`/`FileTransport` as-is: `agent-comms/src/acp/core.py` gains `SendPolicy`-based kind permissions (`models.py`) and the `is_visible_to` predicate (`Conversation.transcript(viewer)` is now viewer-scoped, a breaking signature change fixed at its one call site in `cli.py`); `agent-comms/src/acp/modes.py` (`ModeConfig`/`load_mode`) parses mode YAML into a Roster/SendPolicy/route table; `orchestrator/src/orchestrator/conversation.py` (`OrchestratorConversation`) wraps it in `asyncio.to_thread`, same shape as `MessageMirror`. Stage/claims reuse `FileTransport.stage()`/`.claim()` unmodified. Verified against agent-comms' own suite plus orchestrator's (no-DB and live-Postgres); see docs/decisions.md for the reuse-vs-new-engine call and two lower-confidence defaults (permissive pipeline-mode Roster, optional non-hardcoded moderator role).
- [x] **M2.6** FloorPolicies: `round-robin` and `raise-hand` (one Haiku call scoring all personas). `agent-comms/src/acp/floor.py` (`HandRaise`/`Reason`, closing the M1.1 hand-raise gap; sent as an ordinary `kind="hand-raise"` Envelope) + `orchestrator/src/orchestrator/floor/` (`FloorPolicy` Protocol, `ConversationView`/`Grant`/`Parallel`/`Converged`/`AskHuman`, `RoundRobinFloorPolicy`, `RaiseHandFloorPolicy` with product-owner-confirmed `RaiseHandTuning` defaults, `HandRaiseScorer` Protocol + `AnthropicHandRaiseScorer` + `FakeHandRaiseScorer`). Pure/no-I/O by design; the turn-runner that calls the scorer, runs `policy.next()` and turns a `Grant` into an assignment + wake-up is M2.7, not built here. `AnthropicHandRaiseScorer` is verified only against a canned tool_use response fixture -- no live Anthropic API call was attempted (no credentials/egress assumed available in this dev-team sandbox); see docs/decisions.md for the follow-up smoke-test flag and two other logged calls (the queue-jump-cap interpretation, and the `ConversationView.recent_transcript` field added beyond the architect's original list).
- [x] **M2.7** Moderator agent: opening, rolling summary, stop conditions (rounds, budget, convergence), final synthesis into an `artifact`. `orchestrator/src/orchestrator/moderator/` (`ModeratorRunner` -- opening pass, floor-decision dispatch, Turn-row construction, stop-condition checks; `Summarizer`/`AnthropicSummarizer` for the rolling summary persisted on `Session`; `Synthesizer`/`AnthropicSynthesizer` for final synthesis + an optional 0-or-1 minority-report `Artifact`). `StopRulesConfig` (`orchestrator/src/orchestrator/floor/raise_hand.py`) merges M2.6's `RaiseHandTuning` with `Mode.stop_rules_json`'s intent into one type, parsed from a mode's `stop_rules` block via `acp.modes.load_mode`. New Session columns (`rolling_summary`, `rolling_summary_through_seq`, `stale_argument_count`) + migration. `acp send` gained `--tokens-in`/`--tokens-out`/`--cost` for the required-but-soft-fail budget-accounting convention (docs/agent-communication-protocol.md). `AnthropicSummarizer`/`AnthropicSynthesizer` (and M2.6's `AnthropicHandRaiseScorer`) are verified only via canned response fixtures -- a full end-to-end session needs both the live `sbx` CLI and live Anthropic credentials simultaneously, neither available in this dev-team sandbox; see docs/decisions.md for this and several other logged judgment calls (Session.status vocabulary reuse, the self-emitted hand-raise wire convention, round/streak/budget semantics, and a real alembic logging bug found and fixed along the way).
- [x] **M2.8** REST API: create task, create session (roster + mode), start, interject, stop; SSE stream of messages. `orchestrator/src/orchestrator/api/` (`tasks.py`, `sessions.py`, `deps.py` for FastAPI dependency-injectable `SandboxProvider`/LLM seams, `runtime.py`'s `launch_runner` -- the M2.9-boundary-factored function `/start` calls). New `orchestrator/src/orchestrator/modes_registry.py` resolves `Mode.name` to `modes/<name>.yaml` (eagerly at create-session time, re-validated at start). `Task.created_at` column + migration. `ModeratorRunner` (M2.7, already merged) gained `stopping`/`cancelling` status handling for `/stop`'s `synthesize` flag and interject-addressed hand-raise synthesis. Filled several infrastructure gaps M2.4-M2.7 left unwired (nothing before this ever ran `MessageMirror.run_forever()`; each session now gets its own `PubSub`+mirror+factory directory) -- all logged in docs/decisions.md along with the budget-field-name translation, the `status="active"` correction, and the zero-auth posture (no bearer tokens, no caller identity -- do not expose this service beyond a private network until M5). Tested end-to-end with fakes for `SandboxProvider` and the LLM seams (no live `sbx` CLI or Anthropic credentials assumed available, the same compound gap M2.4/M2.6/M2.7 disclosed).
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
