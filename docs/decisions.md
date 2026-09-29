# Decision log

Newest first. Status: **Decided** or **Proposed** (awaiting sign-off).

| Date | Decision | Status | Rationale / notes |
| --- | --- | --- | --- |
| 2026-09-28 | All agents of a task share one team sandbox (workshop topology: Herdr session per agent, file messages) for now; per-agent sandboxes are a later option | Decided | Simplest path, reuses the workshop end to end; isolation between agents becomes policy-level; see product-design.md (Agent sandboxes) |
| 2026-09-28 | Local Docker Sandboxes only; no Docker cloud sandboxes for this work | Decided | Supersedes the cloud part of the 2026-09-25 sandbox decision; removes TTL, cloud API and remote-MCP concerns |
| 2026-09-28 | Orchestrator, API and `acp` protocol library in Python (FastAPI, asyncio, SQLAlchemy + psycopg, Alembic, SSE) instead of Spring Boot | Decided | One language for the orchestrator and the in-sandbox `acp` CLI; strong agent/LLM ecosystem |
| 2026-09-28 | Default team role catalog: Researcher, Product Owner, Coordinator, Architect, Backend Engineer, Frontend Engineer, QA/Tester; roles are editable data | Decided | Roles are configuration, not code; see agent-communication-protocol.md |
| 2026-09-28 | Agent communication uses a reusable protocol (`acp/1`) derived from the workshop's `handoff`/`crew` design | Proposed | Same contract for Bayto, coding teams and Sened; transport and wake-up are pluggable |
| 2026-09-28 | Raise-hand turn-taking as default for open chat/brainstorm/collaborate; structured rounds for debate; moderator acts as referee | Proposed | Free-for-all causes echo, collisions and no convergence; see research/turn-taking.md |
| 2026-09-27 | Use wad-sbx-workshop as the reference implementation | Decided | Working multi-agent team on Docker Sandboxes; see research/wad-sbx-workshop-analysis.md |
| 2026-09-25 | Pricing: subscription with included usage credits + pay-as-you-go top-ups; BYO API key later | Parked (local-only for now) | Seat-based and per-session don't fit; credits map to token + sandbox cost; see research/pricing.md |
| 2026-09-25 | First user: solo founders / engineers making decisions | Decided | |
| 2026-09-25 | Claude only for the MVP; multi-provider later | Decided | |
| 2026-09-25 | Agent memory scoped to a task (across that task's sessions, never across tasks) | Decided | Each role's Claude session state kept in the task's team sandbox |
| 2026-09-25 | Moderator is an LLM agent | Decided | Floor policy decides speakers; moderator referees and synthesizes |
| 2026-09-25 | Real-time streaming for every agent turn | Decided | |
| 2026-09-25 | Standalone app (not embedded in Sened/Gashana first) | Decided | |
| 2026-09-25 | Sandbox runtime: Docker Sandboxes (sbx), one sandbox per agent; Cloud Sandboxes API in production, local sbx for development | Superseded (cloud part; one-per-agent part) | microVM isolation without running a hypervisor fleet; see research/docker-sandboxes.md |
| 2026-09-25 | Each agent runs in its own sandbox; agents only communicate through the orchestrator | Superseded (for now) | Replaced by the shared team sandbox on 2026-09-28; per-agent isolation kept as a later option |
| 2026-09-25 | Support existing/external agents and an Agent explorer to add agents to a task | Decided | |
| 2026-09-25 | Product name: Bayto (ባይቶ) | Decided | Tigrigna village assembly; see research/naming.md |
