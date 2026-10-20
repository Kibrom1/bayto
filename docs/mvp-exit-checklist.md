# MVP exit test: run sheet

The plan, rubric and pass rules are in [mvp-exit-test.md](mvp-exit-test.md). This is the sheet to work through while you run it. Budget about 20 to 40 minutes of your attention per task; the Bayto run itself takes longer, so start it and do the baseline while it works.

## 0. Before the first task (once)

- [ ] Latest `main` pulled; `scripts/bayto-up.sh` restarted (so the pause note and guards are live). Open http://127.0.0.1:5173.
- [ ] Postgres up; `claude` signed in; `sbx` works (`sbx ls`).
- [ ] The five task prompts written down below, before running anything. Same text for both arms.
- [ ] Folder for the evidence: `mkdir -p docs/mvp-runs`.
- [ ] Sandbox stop and resume (M1.14 follow-up, optional but it is the one open precondition): start any session, wait for the first answer, stop the sandbox by hand (`sbx stop sbx-<task_id>`), then Resume in the UI. Pass = it continues without manual cleanup. Note whether the seats remember the earlier conversation (known unverified).

## 1. The five prompts

Write the verbatim prompt for each. Choose real work with a checkable outcome (see the table in the plan).

| # | Mode | Prompt (verbatim) |
| --- | --- | --- |
| 1 | Debate | |
| 2 | Debate | |
| 3 | Brainstorm | |
| 4 | Open chat | |
| 5 | Debate | |

## 2. Per task (copy this block for each of the five)

**Task #__  slug: ________**

Baseline arm (single agent, no team):
- [ ] Run it from a scratch folder so it sees no repository:
  ```
  mkdir -p docs/mvp-runs/N-slug && cd docs/mvp-runs/N-slug
  claude -p "$(cat prompt.txt)" --output-format json > baseline.json
  ```
  Save the prompt in `prompt.txt` first. `baseline.json` holds the answer plus cost and duration fields; open it once and note the field names (`result`, `total_cost_usd`, `duration_ms` in current releases).
- [ ] Copy the answer text to `baseline.md`. Note cost and duration.

Bayto arm:
- [ ] New task in the UI with the same prompt; pick the mode; seat the roster you would really use. Note: agents ___, mode ___.
- [ ] Start. Do not intervene except to answer a `needs_human` pause. Every intervention goes in the record sheet and counts as "not clean".
- [ ] Wait for the session to finish (status finished, a synthesis artifact exists). Copy the synthesis text to `bayto.md` without editing it.
- [ ] Cost and rounds: `curl -s http://127.0.0.1:8000/tasks/<task_id>/usage-report` and the room header. Note cost ___, rounds ___, duration ___.
- [ ] Keep the transcript: save the room page (or export the `message` table rows for the session) to `transcript.txt`.

Blind scoring (do this after both arms are done, not between):
- [ ] Shuffle and strip names. In `docs/mvp-runs/N-slug/` run:
  ```
  python3 - <<'PY'
  import random, pathlib, json
  a, b = pathlib.Path("baseline.md").read_text(), pathlib.Path("bayto.md").read_text()
  pair = [("baseline", a), ("bayto", b)]; random.shuffle(pair)
  for label, (name, text) in zip("AB", pair): pathlib.Path(f"answer-{label}.md").write_text(text)
  pathlib.Path("key.json").write_text(json.dumps({"A": pair[0][0], "B": pair[1][0]}))
  print("scored files: answer-A.md, answer-B.md; do not open key.json until you have scored")
  PY
  ```
- [ ] Score `answer-A.md` and `answer-B.md` 1 to 5 on correctness, coverage, decision-usefulness, surprise, concision. Then open `key.json` and fill the record sheet.

Record sheet (paste into `record.md`):

```
Task #:            Slug:
Prompt (verbatim):
Mode / roster / agents / rounds:
Baseline: duration, cost, answer link
Bayto:    duration, cost (ledger), synthesis link, transcript link
Scores (baseline | bayto): correctness / coverage / decision-usefulness / surprise / concision
Total (baseline | bayto):
Manual interventions (list; any means "not clean"):
Notes / failures:
```

## 3. Tally (after all five)

| # | Baseline total | Bayto total | Bayto wins? | Clean run? | Cost ratio (Bayto / baseline) |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

- [ ] Quality: Bayto wins at least 4 of 5, and loses by more than 3 points in none.
- [ ] Reliability: all five runs clean (no manual sandbox cleanup, no stuck seat, no lost transcript).
- [ ] Cost: reported for all five; ratio noted (no ceiling set).
- [ ] Artifacts: five folders under `docs/mvp-runs/`, reproducible from the stored prompts.

Result: pass / partial / fail. If pass, write the summary into `work-plan.md`, tick the exit item, and decide the post-MVP phase. If partial or fail, read the transcripts before changing anything (see "Reading the result" in the plan).

## 4. Optional sixth run: Build

Not part of the exit criterion, but cheap now: run `python3 docs/e2e/live/live_build_e2e.py` once more on a day you changed Build code, as a smoke test, and keep `live-build-e2e-out/report.json` under `docs/mvp-runs/build-smoke/`.
