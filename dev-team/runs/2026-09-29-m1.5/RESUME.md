# Resume notes — 2026-09-29

## Where things stand
- **M1.5 done, QA passed.** Branch `sbx/m1.5-compat-run` @ `a31e482`, already pushed to origin. Open the PR and merge it.
  Record of the run in this folder: `transcript.md` (all 6 messages), `messages/`, `state.json`.
- **Follow-up (small):** `agent-comms/docs/m1.5-compat-check.md` says the bash reference's id suffix is "10 chars";
  QA traced it to 6 random chars + the shell PID (variable length). Fix the sentence.
- **Setup fixes** on local branch `sbx/dev-team-setup-fixes` @ `432ce69` (not pushed): qa on Sonnet 5,
  `.claude/settings.json` with `additionalDirectories: /home/agent/work`, ignore `dev-team/runs/`.
  Push it and merge it **before** starting the next sandbox, or the new team hits the same prompts.

## Starting fresh
1. On the Mac, `cd` using the exact case: `~/Desktop/workspace/AI/bayto` (capital **AI**). A path typed as `.../Ai/...`
   got written into the sandbox's `~/work/app-path`; `prepare` then failed and `~/work/app` was never created.
2. `dev-team/run.sh bayto-dev`, then `start-team` in the sandbox shell.
3. If a role never acknowledges, check `crew logs <role>` for a one-time Claude prompt (fullscreen renderer,
   "read outside working directories") and answer it.
4. Give the team the next task:

       crew ask "Task M1.8 (docs/work-plan.md): finish the start-team adaptation to the generated roster, including the notes in the task."

## Watching
- Message feed: `sbx exec -it bayto-dev bash`, then `crew watch`.
- One agent per tab: `sbx exec -it bayto-dev herdr agent attach <role>`. Only watch. Anything typed there goes to
  the agent's Claude session, and Ctrl-C / `/exit` quits it; close the Mac tab to leave.

## Next work-plan candidates
(Recorded in `docs/work-plan.md` on branch `sbx/plan-next-up` @ `e083f3f`: "Next up" pointer, M1.5a, M1.8 lessons.)
- M1.8 remainder: adapt `start-team` to the generated roster (and keep the readiness acks).
- M1.9 remainder: pass the generated `tools.json` allow/deny lists to each Claude session.
- W0.5: `.gitignore` / license / version-pinning note.

## Cleanup
`exit` the sandbox shell, then on the Mac: `sbx env rm dev-team/.build/factory/sbxenv.yaml --env-arg name=bayto-dev`.
