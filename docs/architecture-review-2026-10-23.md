# Architecture and product review, 2026-10-23

Reviewer view: researcher and architect. Based on `main` at `7781095` (PRs through #193), the project docs, the work plan and a read of the API surface. Nothing here was run against a real provider, a real `sbx` sandbox or a real Build session.

## State of the project

The earlier gaps are closed: Build writes to `.git` are denied and guarded, one running session per task, CI exists and gates merges, per-seat models and providers, per-seat retrieval with role visibility, recall of earlier sessions, an optional embedding stage, and a feature e2e harness. The risks that remain are mostly about running Bayto anywhere other than one trusted laptop, and about what a user can take away from a session.

## Findings, most valuable first

| # | Finding | Why it matters | Action |
|---|---|---|---|
| 1 | **No authentication.** The Host check blocks DNS rebinding, but anyone who can reach the port can read every transcript and start Build sessions. | Blocks any shared or remote use, and a mis-bound port is one typo away. | Optional shared-secret login (`BAYTO_API_TOKEN`): bearer header or HttpOnly cookie, SSE works, off by default. |
| 2 | **Nothing leaves the room.** A finished session has a synthesis and a transcript, but no single decision record to hand to someone else. | The product's output is a decision; users copy it by hand. | Export a session as Markdown: question, roster, synthesis, minority report, key turns, cost, sources read. |
| 3 | **Past sessions cannot be found.** Recall feeds agents, but a person cannot search across transcripts. | Value grows with history; without search it is a pile. | Full-text search over messages and syntheses, grouped by task and session. |
| 4 | **Provider adapters have thin failure handling.** Direct HTTP seats (OpenAI, Gemini, Ollama) are new and unproven live. | One 429 or slow response should not end a seat's session. | Timeouts, bounded retry with jitter for 429 and 5xx, a clear failure reason, tests with the mock server. |
| 5 | **Interrupted sessions cannot resume** after an orchestrator restart (they become `orphaned`). | Real restarts happen; users lose context. | Not done here: needs sandbox/seat resume proven on a real `sbx`. Existing "continue" with recall is the workaround. |
| 6 | **Everything real is unverified.** Real provider, gateway, embeddings, sbx and Build-with-PR have only run against fakes. | The largest remaining risk. | Needs the Mac and keys; listed in the hand-off. |
| 7 | **`moderator/runner.py` is the centre of gravity** (about 1,000 lines). | Each mode adds branches. | Split by mode when the next mode lands; not worth a refactor alone. |
| 8 | Small: dark-mode contrast of the "Jump to latest" pill; no task archive in the UI. | Polish. | Fix contrast; archive stays at session level. |

## Comparable tools (for context)

Group-chat and debate orchestration is a common pattern in agent frameworks (round-robin or selector-driven group chat, moderated debate with a judge or aggregator). Typical weaknesses reported: runaway turn counts, agreement by imitation, and no durable output. Bayto already addresses the first two with a raise-hand floor policy, budget stops and a minority report. Finding 2 targets the third.

Sources: [Group chat orchestration pattern](https://handsonai.info/agentic-building-blocks/agents/orchestration-patterns/group-chat/), [AgentScope multi-agent debate](https://java.agentscope.io/en/multi-agent/multiagent-debate.html).

## Plan for this round

Four parallel branches: auth (1), export (2), search (3), provider resilience plus contrast (4, 8). Items 5 to 7 are recorded, not built.
