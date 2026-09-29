# M0 debate spike

Goal: prove a 3-round Claude debate works inside one Docker Sandbox, reusing the vendored workshop (`vendor/wad-sbx-workshop`), and learn where it breaks.

Role mapping (the workshop's `handoff` only accepts fixed names): coordinator = moderator, developer = advocate, qa = skeptic. Briefs are in `roles/`; `run.sh` copies the vendored tree to `.build/` (git-ignored), swaps in these briefs and `PROMPT.md`, and launches.

## Run (on your Mac)

    cd ~/Desktop/workspace/AI/bayto
    spikes/m0-debate/run.sh bayto-m0

In the sandbox shell:

    start-team
    herdr agent list
    crew ask "$(cat spikes/m0-debate/topics/01-microservices.txt)"   # or paste the topic text
    crew watch

The topics folder is inside this repo, not the mounted workspace, so paste the topic text if `cat` cannot find it.
Save each run: copy `~/work/factory/messages/*.json` and the moderator's summary into `runs/<topic>/`.
Cleanup: `exit`, then `sbx env rm .build/factory/sbxenv.yaml --env-arg name=bayto-m0`.

## Findings to record (FINDINGS.md)

Token use per round, echo/dominance/stall failures, wake-up reliability (crew-notify exit codes), whether 3 rounds were enforced.
