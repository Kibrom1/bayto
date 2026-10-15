# Task: whole-project review of Bayto (paste into New task)

**Title:** Bayto whole-project review: product, architecture, design, usability, testing

**Output type:** project review

**Brief:**
Review Bayto as a whole, using the repo as evidence (README.md, docs/, orchestrator/src, agent-comms/,
kits/, envs/, web/src, scripts/). Each seat reviews from its own angle, then the group reconciles.
- PM: is this the right MVP? Target user and job to be done, scope, what is built / partial / missing against
  docs/mvp-exit-test.md, and whether the 5-task exit test is the right next gate. Prioritised backlog (top 10).
- Architect: soundness of the orchestrator/moderator/sandbox/ACP design, failure modes (sandbox start, resume after
  stop, budgets, subscription vs API access), security and data boundaries, tech debt, and what will not scale.
- UX Designer (US designer): every screen (Tasks, New task, Session setup, Room, Synthesis, Services): hierarchy,
  visual polish, copy, dark mode, mobile, empty/error states, consistency. Findings ranked by severity.
- Customer (usability): walk the first-run path from opening the app to a first synthesis as a new user.
  Where would they get stuck, what is unclear, what would make them quit.
- QA Tester (end-to-end testing): the end-to-end test plan: critical flows, edge cases, what is covered by
  existing tests vs not, and concrete test cases to add (setup -> start -> stream -> interject -> pause/resume ->
  stop/synthesize, plus failure paths).
The Architect must challenge the PM's priorities and the QA Tester must challenge every "this works" claim.
Record unresolved disagreements as a minority report.

**Success criteria:**
One synthesis with: a verdict per area (PM, architecture, design, usability, testing), a severity-ranked findings
table (each citing a file or screen), an E2E test plan with concrete cases, a top-10 backlog, and a go/no-go on the
exit test. No generic advice.

**Session setup:** mode = Review (or Debate), roster in this order: PM, Architect, UX Designer, Customer, QA Tester.
Budget: Deep preset ($15 / 600k tokens): five seats over several rounds.
