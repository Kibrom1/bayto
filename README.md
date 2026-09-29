# Bayto (ባይቶ)

A multi-agent discussion platform: pose a task or topic, seat a panel of AI agents
(together in one local Docker Sandbox per task), and let them chat, collaborate and debate their way
to a concrete artifact. *Bayto* is the traditional Tigrigna village assembly where the
community gathers, debates an issue openly and decides together.

Status: implementation started: `agent-comms/` (acp package, FileTransport, 7 conformance tests passing). Local sbx spikes (W0, M0) need to run on your Mac. Start with [docs/work-plan.md](docs/work-plan.md).

## Documents

| File | What it covers |
| --- | --- |
| [docs/product-design.md](docs/product-design.md) | Product and system design: concepts, modes, agents, explorer, sandboxes, orchestration, UI, architecture, roadmap, decisions |
| [docs/agent-communication-protocol.md](docs/agent-communication-protocol.md) | Reusable agent-to-agent communication protocol (`acp/1`), team roles and modes |
| [docs/work-plan.md](docs/work-plan.md) | Task-level plan to start implementing (W0, M0–M2 in detail, M3–M6 deliverables) |
| [docs/decisions.md](docs/decisions.md) | Decision log with dates and rationale |

## Research

| File | What it covers |
| --- | --- |
| [research/docker-sandboxes.md](research/docker-sandboxes.md) | Docker Sandboxes (sbx): isolation model, local sbx usage (cloud kept for reference only), limits, pricing |
| [research/sandbox-runtime-comparison.md](research/sandbox-runtime-comparison.md) | Firecracker vs gVisor vs hardened Docker containers vs Docker Sandboxes |
| [research/wad-sbx-workshop-analysis.md](research/wad-sbx-workshop-analysis.md) | Analysis of the wad-sbx-workshop reference repo and how it maps to Bayto |
| [research/turn-taking.md](research/turn-taking.md) | Moderator-picked vs self-selected speakers; the raise-hand model |
| [research/pricing.md](research/pricing.md) | Pricing model options and recommendation |
| [research/naming.md](research/naming.md) | Name exploration (English, Amharic, Tigrigna) and the choice of Bayto |

## Live versions

These files are snapshots (2026-09-28) of living docs on claude.ai:

- Product design: https://claude.ai/code/artifact/765aedfd-2f81-4f54-9ad3-db7161a73404
- Agent Communication Protocol: https://claude.ai/code/artifact/0c99203a-4ac7-4165-abd3-e9418220563b

## Reference implementation

- https://github.com/Kibrom1/wad-sbx-workshop (fork of shelajev/wad-sbx-workshop)
