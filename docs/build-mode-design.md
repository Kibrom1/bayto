# Build mode: from discussion to working code (MVP scope)

2026-10-15 · Owner: Kibrom · Status: proposed, needs sign-off on the open questions at the end

## Why

Bayto's users are developers. Debate, Open chat and Brainstorm end in a synthesis, which is advice. A developer's next move is "now build it", so the MVP needs a mode where the roster writes, tests and reviews code and hands back something the user can merge.

## What exists today (checked in the repo)

- Every turn is a headless `claude -p` inside the team sandbox, run from `$HOME/work/app` (`TURN_SCRIPT` in `sandbox/local.py`), with the role's own tool-flag file.
- Those tool-flags are **read-only today**: `Read, Grep, Glob, Bash(handoff|crew)` allowed, `Write, Edit, WebFetch` denied, `--permission-mode dontAsk`. So discussion seats cannot change files.
- `ModeConfig` already accepts `floor_policy: pipeline` and a `route` table, and `send_roster()` handles it. The orchestrator has no pipeline floor policy: `floor/` has only `round_robin` and `raise_hand`.
- The runner already has a phase machine (M2.12) and the seeded templates already include Developer, QA Tester and E2E Tester.
- The `dev-team/` flow (the workshop heritage) already mounts a repo read-write and has engineers commit to `sbx/<task>` branches. Bayto tasks have no such workspace yet: `task` has only title, brief, output_type, success_criteria.

## Design

### 1. Build is its own mode, not a brainstorm phase

`modes/build.yaml`, `floor_policy: pipeline`. Deliberation and execution stay separate because their stop rules differ: a debate stops when arguments go stale; a build stops when the tests pass.

Pipeline route (sequential, so there is never more than one writer in the worktree):

```
start                        -> architect          # turns the brief/synthesis into a plan + acceptance checks
architect.report.planned     -> developer
developer.report.implemented -> tester
tester.report.pass           -> reviewer
tester.report.fail           -> developer          # fix cycle
reviewer.report.approve      -> done
reviewer.report.changes      -> developer          # fix cycle
```

Stop rules: `max_fix_cycles: 3` (then `needs_human`, not failure), per-turn wall clock, the existing dollar budget, user stop. Success = tester pass AND reviewer approve.

### 2. Workspace and branch

- Add `workspace` to the task: `{repo_path, base_branch, test_command}` (all optional except `repo_path` for Build).
- The sandbox mounts `repo_path` read-write. Before the first build turn the orchestrator creates branch `bayto/<task-slug>` from `base_branch`.
- Agents commit to that branch inside the sandbox. **They never push and have no git credentials.** The user reviews and merges on their own machine. Pull-request creation is a later, opt-in feature.
- Use git inside the sandbox for all writes. The design notes already warn that `cp` on virtiofs has produced NULL-filled files.

### 3. Per-role tool permissions (the real security boundary)

| Role | Allowed | Denied |
| --- | --- | --- |
| Architect | Read, Grep, Glob | Write, Edit, Bash except `acp` |
| Developer | Read, Grep, Glob, Write, Edit, `Bash(git add/commit/status/diff/log)`, the project's test and build commands | `git push`, `git remote`, `rm -rf`, `curl`/`wget`, WebFetch, anything outside the worktree |
| Tester | Read, Grep, Glob, Bash(test command), read-only git | all writes, git commit |
| Reviewer | Read, Grep, Glob, `Bash(git diff/log/show)` | Write, Edit |

Implemented as new generated `tool-flags/<role>` files per mode. A missing flag file keeps today's behavior: the turn refuses to run. Network egress stays on the sandbox allowlist (package registries only).

### 4. Evidence, not claims

A `report` envelope from the tester must include the command run, its exit code and the tail of its output. The moderator rejects a `pass` report without them (the template text for E2E Tester already asks for this separation of "ran" versus "reasoned about"). The reviewer reads the actual `git diff`, not the developer's summary.

### 5. Output

New artifact type `change_set`:

```
{ branch, base_branch, commits[], diff_stat, tests: {command, exit_code, tail},
  review: {verdict, notes[]}, open_issues[], summary }
```

The room shows it in place of the synthesis: branch name with a copy button for `git switch <branch>`, a diff viewer, test evidence, reviewer notes and fix-cycle count. The existing synthesis callout style is reused for "ready to merge" versus "needs you".

### 6. Brainstorm/Debate to Build handoff

"Continue as Build" on a finished session creates a new session in the **same task** (same sandbox, same factory dir), seeded with the synthesis artifact as the architect's input brief and the minority report as risks to address. This is the cheap first version of the roadmap's "mode pipelines". The roster is suggested (Architect, Developer, QA Tester, Reviewer) and editable in Setup.

### 7. Setup UI

- Mode card "Build" with a one-line blurb ("Plan, code, test and review on a branch. You merge.").
- When Build is chosen, Setup shows required fields: repo path, base branch, test command, with a preflight (path exists, is a git repo, working tree clean).
- Cost estimate uses the larger Build defaults and warns more strongly, since build turns use tools heavily.

## Work breakdown

| # | Task | Done when | Effort |
| --- | --- | --- | --- |
| B1 | `task.workspace` column + migration + API + Setup fields + preflight | Task can store and validate a repo; tests cover bad paths and dirty trees | 1 day |
| B2 | Branch + rw mount in the provider (`create`, branch creation, no credentials) | Live check: branch exists in the sandbox, host repo untouched on `main` | 1-2 days |
| B3 | `floor/pipeline.py` + runner wiring + `max_fix_cycles` + `needs_human` on exhaustion | Unit tests with fakes walk plan, implement, fail, fix, pass, approve | 2 days |
| B4 | `modes/build.yaml`, Architect and Reviewer templates, seed update | `seed.py` creates the mode and roles; mode tests pass | 0.5 day |
| B5 | Per-role tool-flag generation for Build, with denylist tests | Test proves Developer cannot `git push`/`curl`; Reviewer cannot Write | 1 day |
| B6 | Evidence check in the moderator (reject `pass` without command/exit code) | Unit test: a bare "pass" is bounced back to the tester | 0.5 day |
| B7 | `change_set` artifact + synthesizer variant for Build | Artifact built from git, not from model prose | 1 day |
| B8 | Web: Build card, workspace fields, change-set view, "Continue as Build" | Component tests + screenshots | 2 days |
| B9 | Scripted live E2E `docs/e2e/live/` for Build on a throwaway repo | Script goes red to green: a seeded failing test is fixed on the branch | 1 day |

About 10 working days. B3 and B5 carry the risk; B2 needs the Mac's `sbx`.

## Risks

- **Prompt injection and runaway commands.** The denylist is the control, so B5's tests are not optional. The VM protects the host; the branch protects the repo.
- **Fix loops burn budget.** Hence `max_fix_cycles` and the dollar cap, with a `needs_human` stop that keeps the work.
- **Tester writes tests that pass trivially.** Mitigation: the architect states acceptance checks first, and the reviewer checks that tests exercise them.
- **Large repos exceed context.** The architect's plan lists the files to touch; the developer is told to stay within them.

## Open questions

1. **Test command:** required in Setup, or auto-detected (package.json, pyproject, pom.xml) with an override? Recommend: required field with a detected default.
2. **One repo per task** for the MVP? Recommend yes.
3. **Commit policy:** the developer commits per logical step, or one commit at the end? Recommend per step, so the diff is reviewable and revertable.
4. **PR creation** stays out of the MVP (needs a GitHub credential in the sandbox). OK?
5. **Roster:** fixed four roles, or let the user add specialists such as UX and E2E tester? Recommend a default four with extras allowed, as read-only reviewers.

## Decisions made while building (2026-10-15)

- The tester does **not** write files. Path-scoped rules such as `Write(tests/**)` are unverified under `claude -p`, and an unscoped Write is not a "tests only" seat. The developer writes the tests; the tester fails the build when they are missing.
- Role keys are `build-architect`, `build-developer`, `build-tester`, `build-reviewer`, so they never overwrite the discussion templates.
- Routing is on a `VERDICT: <word>` line at the end of each turn, parsed by the orchestrator (not an envelope field).
- A tester `pass` must show a command and an exit code of 0 (`require_evidence` in the mode's `stop_rules`); otherwise the turn is repeated once with a note saying why, then the session pauses for a person.
- The test command is limited to plain steps joined by `&&` (no pipes, redirects, `;`, `$(...)`), checked when the task is created, because each step becomes an allow rule.
- The session's branch is `bayto/build-<first 8 chars of the session id>`; the `change_set` artifact reads it from git.
