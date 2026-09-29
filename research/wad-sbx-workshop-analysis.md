# Research: wad-sbx-workshop as Bayto's reference implementation

2026-09-27/28. Repo: https://github.com/Kibrom1/wad-sbx-workshop (fork of shelajev/wad-sbx-workshop, a two-hour Docker Sandboxes workshop that builds a "software factory" of coding agents). Local copy: `~/Desktop/workspace/AI/sandbox/wad-sbx-workshop`.

## What the workshop builds

A coordinator, developer and QA agent work on a task inside **one** Docker Sandbox. They exchange file messages, use a host task backlog (Beans) through the SBX MCP gateway, and ask a human for product decisions over SSH. Roles can run Claude Code, Pi or Codex with a chosen provider/model per role.

| Chapter | Adds |
| --- | --- |
| 0 Setup | sbx, accounts, sample app (incident triage board, API + PostgreSQL) |
| 1 One agent | Isolated execution, mounted project, containers inside SBX, published port |
| 2 Repeatable env | `sbxenv.yaml` + a Beans task |
| 2.5 Shared guidance | A simple kit, then the ACR coding-policy kit |
| 3 Another assistant | Pi, provider/model per role |
| 4 Team | Herdr sessions, roles, file messages |
| 5 Host tools | MCP gateway, scoped host tools, network controls |
| 6 Human | SSH in, answer a product question, team resumes |
| 7 Reuse | Same factory on another repo |
| 8 Presenter | `sbx inspect`, runtime mounts (`sbx mount/umount`), `--cloud`, org governance |

## Key building blocks

- **`sbxenv.yaml`** — declares the sandbox: name/args, agent, workspace, cpus/memory, ports, kits, MCP servers. Created with `sbx env create FILE --env-arg ...`, removed with `sbx env rm`.
- **Kits** (`spec.yaml`, `kind: mixin` or `sandbox`) — reusable installs (`setup.install`, `setup.startup`), `permissions.network.allow`, `args`. `kind: sandbox` kits can declare **credentials**: API keys/OAuth injected by the SBX proxy per domain so real secrets never enter the VM (`kits/multi-provider`).
- **`team.tsv`** — `role  harness  provider  model` per line.
- **`support/bin/handoff`** — file message bus: atomic writes, run/task/attempt-scoped ids, global `seq` under a lock, per-recipient consumption, `ack` separate from `report`, reports with checks, single-fire `claim`, `stage` state, bounded `wait`, `doctor`.
- **`support/bin/crew-notify`** — wakes a role through Herdr only if it isn't `working`; reports `blocked`; inspects the terminal when status is `unknown`; fires once per message via a claim. Exit codes 0/3/4/5/9.
- **`support/bin/crew`** — human interface: `submit`, `ask`, `reply`, `watch`, `status`, `logs`, `deliver`.
- **`support/bin/start-team`** — starts Herdr, creates a pane per role, starts each agent with its model, sends the role brief and waits for readiness acks.
- **`support/roles/*.md`** — role contracts. Coordinator never edits code; "send, then end your turn; the reply wakes you"; never poll; record human decisions in `decisions/<task>-a<attempt>.json`.
- **MCP gateway** — `mcp.servers` in sbxenv (or `sbx mcp add` + `--static-mcp`) runs a host MCP server (`beans-mcp`) that exposes only list/get task and append note.

## Mapping to Bayto

| Workshop | Bayto |
| --- | --- |
| One sandbox, Herdr pane per role | Same for now: one team sandbox per task, a Herdr session per agent; the orchestrator owns the floor and the transcript. Per-agent sandboxes are a later option |
| `handoff` files in `$FACTORY_DIR` | `acp/1` with the same file layout (FileTransport); Postgres mirror for the UI. Postgres/HTTP transports later |
| `crew-notify` + Herdr | Wake-up SPI: Herdr (default), `sbx exec` headless turns, webhook, UI |
| `crew` CLI | Bayto room actions |
| `team.tsv` | Session roster, generated |
| `roles/*.md` | Role catalog (Researcher, PO, Coordinator, Architect, BE, FE, QA) |
| Fixed coordinator → developer → QA | Pluggable FloorPolicy (pipeline, raise-hand, round-robin, parallel-blind) |
| Beans MCP adapter | Bayto MCP server with narrow tools |
| `sbx --cloud` demo | Not planned (local sandboxes only) |

## Gaps and risks it surfaces

- Shared storage between *sandboxes* doesn't define who reads a message or when an agent wakes (presenter notes). Inside one shared sandbox that gap doesn't apply, because `handoff`/`acp` define recipients, consumption and wake-ups. It returns only if Bayto adds isolated per-agent sandboxes.
- A host MCP server is fine for local sandboxes, which is Bayto's only target for now.
- Versions are pinned everywhere (`scripts/versions.env`, kit args); Bayto should do the same.
- Tested platforms: macOS (Apple silicon) and Windows with Git Bash; Linux best-effort.

## State of the local fork (2026-09-28)

- `factory/team.tsv`: coordinator on Pi, developer and QA on Claude Code, all `anthropic claude-haiku`.
- `factory/chapter.env`: `TASK=wad-103`, `MODE=mcp`, ACR on, shell session; `factory/sbxenv.yaml` includes the ACR, Pi, Herdr and a `browser-access` kit (allows `cdn.playwright.dev`) plus the Beans MCP server.
- Uncommitted local change in `chapters/support/bin/prepare`: case-insensitive fallback when resolving the mounted app path (macOS is case-preserving).
