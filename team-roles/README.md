# team-roles

The seven default team roles from docs/agent-communication-protocol.md's "Team roles"
table, transcribed one file per role, exactly the format that doc's "Role definition
format" section describes (M2.11).

**Not** the same thing as the repo-root `roles/` directory, and deliberately kept out of
it: `roles/*.yaml` + `roles/*.md` is the *this-dev-team-factory's own* real, load-bearing
catalog (`acp.roster.load_catalog`/`build_team`, M1.8/M1.9) -- it's what actually assembled
the sandboxed team building Bayto itself, already has 8 entries (the 7 below plus
`code-reviewer`), and uses a different, harness-facing schema (`id`/`brief`/`receives`/
`tools: {allow, deny}`/`team_needs`) geared at spinning up real Claude Code processes with
concrete tool permissions. Writing this task's content there would have silently
overwritten that live catalog. See docs/decisions.md, 2026-09-30, for why this ended up
here instead of at the `roles/coordinator.yaml`-style path the architect's spec assumed
(written without repo access).

These files are reference/documentation content for humans authoring new Bayto modes --
there is no runtime loader for them anywhere in `orchestrator/` or `agent-comms/` today (a
mode's `roles.<name>.may_send` list in `modes/*.yaml` is fully self-contained and never
references one of these files). If that changes, point the new loader here and update this
note.

| File | Role |
| --- | --- |
| `researcher.yaml` | Researcher |
| `product-owner.yaml` | Product Owner |
| `coordinator.yaml` | Coordinator |
| `architect.yaml` | Architect |
| `backend-engineer.yaml` | Backend Engineer |
| `frontend-engineer.yaml` | Frontend Engineer |
| `qa-tester.yaml` | QA/Tester |
