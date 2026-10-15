# Task: full product analysis of Bayto (for the New task form)

Paste the fields below into **New task** in the Bayto UI.

**Title:** Product analysis of Bayto: requirements, UX, risks, backlog

**Output type:** product analysis

**Brief:**
Analyse Bayto as a product and recommend what to build next. Ground every claim in the repo
(read `README.md`, `docs/product-design.md`, `docs/mvp-exit-test.md`, `docs/work-plan.md`,
`docs/decisions.md`, `web/src/`, `orchestrator/src/`). Cover, in order:
1. Target user and the job to be done. Who needs a panel of agents to debate a task, and why not one agent?
2. Requirements: what the MVP must do, what exists today, what is missing. Mark each item built / partial / missing.
3. UX review of every screen (Tasks board, New task dialog, Session setup, Room, Synthesis, Services):
   friction, clarity, empty/error states, accessibility, and the first-run path from "open app" to "first synthesis".
4. Risks and weak assumptions: cost per session, moderator needing an API key vs subscription-only,
   resume after stop, multi-seat streaming, trust in the synthesis.
5. A prioritised backlog (top 10, each with why and rough size) and a recommendation on whether the
   MVP exit test (5 real tasks vs a single-agent baseline) is the right next gate.
The Skeptic seat must challenge the strongest claim in each round; record any unresolved disagreement as a minority report.

**Success criteria:**
A synthesis with sections: user and job, requirements table (built/partial/missing), UX findings ranked by severity,
risks, top-10 backlog, and a clear go/no-go on the exit test. No generic advice: every finding cites a file or screen.

**Session setup:** mode = Review (or Debate if Review is not listed), roster in this order:
PM (requirements), Customer (UX/user advocate), Historian (skeptic), Architect (feasibility, optional).
Budget: Standard preset ($5 / 200k tokens) for the first run.
