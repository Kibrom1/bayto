# Bayto architecture

Several AI agents take turns on one task. A web page talks to one orchestrator. The orchestrator decides who speaks, builds each agent's prompt, sends the turn to a model, and records everything in Postgres. Local first, no accounts, your own Claude login.

The diagrams are Mermaid, so GitHub draws them in the page. Keep them in step with the code when components change.

## 1. What runs where

The browser never talks to a model. Every turn goes web app → orchestrator → a model, and every result is written to Postgres before it is streamed back. Discussion seats can use other providers; Build seats run only in the sandbox.

```mermaid
flowchart LR
  subgraph MAC["Your Mac"]
    WEB["<b>Web app</b><br/>React + Vite :5173<br/>Tasks, Setup, Room,<br/>Change set, Services"]
    CTRL["<b>Control panel</b><br/>bayto-control.py :8787"]
    subgraph ORCH["Orchestrator (FastAPI :8000)"]
      API["<b>API</b><br/>tasks, sessions, agents,<br/>attachments, search,<br/>export, auth"]
      MOD["<b>Moderator runner</b><br/>turn loop, modes,<br/>raise-hand floor policy,<br/>budget and stop rules"]
      CTX["<b>Context builder</b><br/>brief, summary, excerpts,<br/>recall, role visibility"]
      ROUTE["<b>Model router</b><br/>per-seat model, retries,<br/>usage-limit detection,<br/>local-model fallback"]
      HELP["<b>Helpers</b> (claude -p)<br/>summary, synthesis,<br/>hand-raise"]
      GUARD["<b>Build guards</b><br/>tool permissions, .git guard,<br/>test evidence, change set"]
      AUDIT["<b>Audit trail</b><br/>+ OpenTelemetry"]
    end
    PG[("<b>Postgres</b><br/>tasks, sessions, messages,<br/>chunks, audit, fallback state")]
    SBX["<b>sbx team sandbox</b><br/>one claude seat per role<br/>agents talk over acp/1"]
    REPO["<b>Your repo clone</b><br/>Build mode only"]
  end
  subgraph OUT["Outside"]
    ANTH["<b>Anthropic Claude</b><br/>your Claude login"]
    PROV["<b>OpenAI, Gemini, Ollama</b><br/>direct HTTP,<br/>discussion seats only"]
    GW["<b>Model gateway</b><br/>optional"]
    GH["<b>GitHub</b><br/>pull requests"]
    JAEG["<b>Jaeger</b><br/>optional"]
  end

  WEB -->|"REST + SSE"| API
  WEB -->|"Services page"| CTRL
  CTRL -.->|"start / stop"| API
  API --> MOD
  MOD --> CTX
  CTX --> ROUTE
  MOD --> HELP
  MOD --> GUARD
  MOD --> AUDIT
  API -->|"SQL"| PG
  MOD -->|"wake seat, read reply"| SBX
  SBX -->|"claude CLI"| ANTH
  HELP -->|"claude CLI"| ANTH
  SBX ---|"repo mounted read-write"| REPO
  ROUTE -->|"chat"| PROV
  ROUTE -.->|"gateway/..."| GW
  GUARD -->|"gh pr"| GH
  AUDIT -.->|"spans"| JAEG
```

## 2. One turn, including the usage-limit path

The prompt is built once and reused, so a fallback seat sees the same context Claude would have. The fallback path exists only when the task opts in. Otherwise a usage limit pauses the session instead of changing the model quietly.

```mermaid
flowchart LR
  S1["<b>1 Choose speaker</b><br/>raise-hand scores,<br/>budget and round checks"]
  S2["<b>2 Build prompt</b><br/>brief + rolling summary,<br/>best excerpts for this role,<br/>only what the role may see"]
  S3["<b>3 Route</b><br/>Claude seat in sandbox,<br/>or direct provider,<br/>or gateway"]
  S4{"<b>4 Check reply</b><br/>usage-limit shape?<br/>error, empty, verdict"}
  S5["<b>5 Record + stream</b><br/>message, usage, audit,<br/>live to the room (SSE)"]
  FB["<b>Local-model fallback</b><br/>retry this seat once on Ollama,<br/>same prompt as step 2,<br/>badged Local model,<br/>back to Claude after the reset"]
  PAUSE["<b>Pause for a person</b><br/>reason shown in the room"]

  S1 --> S2 --> S3 --> S4
  S4 -->|"normal reply"| S5
  S4 -->|"usage limit,<br/>fallback on"| FB
  FB -->|"answer"| S5
  S4 -->|"usage limit, but Build seat<br/>or fallback off"| PAUSE

  classDef warn fill:#fbead4,stroke:#b45309,color:#1b2420;
  class FB,PAUSE warn;
```

Build seats never fall back: they need tools and history that a local model path does not have.

## 3. Build mode: from brief to pull request

Each role ends with a `VERDICT:` line that routes the next step. Fix cycles are bounded, and anything unroutable stops the session for a person instead of looping.

```mermaid
flowchart LR
  A["<b>Architect</b><br/>plan and scope"] -->|"VERDICT"| D["<b>Developer</b><br/>edits and commits"]
  D -->|"done"| T["<b>Tester</b><br/>runs the test command"]
  T -->|"pass"| R["<b>Reviewer</b><br/>reads the diff"]
  R -->|"approve"| CS["<b>Change set</b><br/>diff, stats, reports<br/>ready_to_merge or blocked"]
  CS --> YOU(["<b>You</b><br/>review, open the PR, merge"])
  T -->|"tests fail: fix cycle"| D
  R -->|"changes requested"| D
```

Guards that apply throughout:

- **Before any host git command**, `.git` config and hooks are restored from the session baseline, so an agent cannot plant a hook that runs on your machine.
- **A pass needs evidence**: a test command and exit code 0. A missing `VERDICT:` pauses the run.
- **No push, no network, one running session per task.** The repo must be clean to start, and it stays on the build branch afterwards.

## Design choices worth knowing

- **Local first, no accounts.** One orchestrator, one Postgres, your own Claude login. Sign-in is an optional shared token (`BAYTO_API_TOKEN`) for when the port is reachable by others; put TLS in front in that case.
- **Context is chosen per seat.** Each seat gets the excerpts most relevant to its role and the current question, filtered by who may see each attachment, within a fixed budget.
- **Everything is recorded.** Messages, usage, stop reasons, fallbacks and exports are stored and audited, so a session can be exported as a decision record.
- **Changes need a person.** Build never pushes or merges. Its output is a branch, a change set and a pull request that you open.

See also: [product design](product-design.md), [Build mode design](build-mode-design.md), [per-agent models](per-agent-models.md), [per-agent context](per-agent-context-rag.md), [audit trail](audit-trail.md).
