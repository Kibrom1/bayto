# MVP exit test (proposed)

Status: draft for Kibrom to adjust, 2026-10-04. Source of the exit criterion: [work-plan.md](work-plan.md) ("5 real tasks run end to end, artifacts kept, judged better than single-agent answers") and the M6 "done when" in [product-design.md](product-design.md) ("5 real tasks run end to end locally without manual cleanup; cost per session is reported"). Numbers marked *proposed* are suggestions, not decisions.

## What it proves

A debated answer from a Bayto team is better than one agent's answer to the same task, and the product runs a whole task without you babysitting the sandbox.

## Preconditions (do these first)

- [ ] M1.12 measured, agent cap and sandbox size recorded (open).
- [ ] M1.15 closed: GH_TOKEN confirmed a stand-in, the three open hosts decided (open).
- [ ] Live streaming works in the room (M1.13 verdict: the headless turn runner is needed in M2; check it is built before counting "live streaming" as met).
- [ ] Resume after a sandbox stop works (M1.14: the `team-started` marker must be cleared; `SandboxProvider.restart_team` exists on `sbx/m5-restart-team` but nothing calls it yet).
- [ ] Token budget set per session so a run cannot overspend (M6).

## The 5 tasks

Pick real work you would otherwise do alone, with a checkable outcome. Aim for a spread, for example:

| # | Kind | Example question | Mode |
|---|---|---|---|
| 1 | Product/strategy decision | Sened vs Gashana: where does the next agent feature go first? | Debate |
| 2 | Technical design choice | A design trade-off in a current project, with two or more real options | Debate |
| 3 | Idea generation | Candidate agent products that fit your no-heavy-marketing rule | Brainstorm |
| 4 | Review/critique | Review a real spec or PR from several roles | Open chat |
| 5 | Plan | A concrete work plan with owners, risks and order | Debate |

Write each task prompt once, before running anything, and use the identical text for both arms.

## Procedure per task

1. **Baseline arm:** one Claude session, same model family, same prompt, no team. Save the answer. Time and cost it.
2. **Bayto arm:** run the task in Bayto with the default team (or the roster you would really use). Let it finish and take the moderator's final synthesis. Do not edit the output. Record sandbox size, number of agents, rounds, duration and the cost the ledger reports.
3. **Blind judging:** remove tool names and formatting tells, put the two answers in random order, and score them yourself against the rubric below. Ideally also have one other person score a sample.
4. **Keep the artifacts:** transcript, final synthesis, baseline answer, scores, cost, any manual intervention. Store under `docs/mvp-runs/<n>-<slug>/` (or the Bayto task record, if that keeps them).

## Rubric (score each 1-5, per arm)

| Criterion | Question |
|---|---|
| Correctness | Are the claims right and checkable? |
| Coverage | Did it surface the options, risks and counterarguments a careful person would? |
| Decision-usefulness | Could you act on it without redoing the thinking? |
| Surprise | Did it contain something you would not have reached alone? |
| Concision | Is the answer usable without wading through the debate? |

The pass rule and the cost rule below are *proposed*; change them if they do not match what "better" means to you.

## Pass criteria (proposed)

- **Quality:** Bayto beats the baseline on total rubric score in at least 4 of 5 tasks, and loses badly (more than 3 points) in none.
- **Reliability:** all 5 runs finish without manual sandbox cleanup, a stuck seat that needs a human to approve a prompt, or a lost transcript.
- **Cost:** cost per session is reported for every run. Record the Bayto/baseline cost ratio; the MVP does not set a ceiling, but a ratio you would not pay for real work is a finding.
- **Artifacts:** all 5 transcripts, syntheses and scores are kept and reproducible from the stored prompts.

## Record sheet (one per task)

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

## Reading the result

- 4-5 wins and clean runs: the MVP exit is met; write the summary into work-plan.md and decide the post-MVP phase.
- Wins only on some task kinds: note which; that tells you which modes to ship first.
- Few wins: the debate adds cost without quality. Look at the transcripts before changing anything: weak roles, too many rounds, or a moderator that flattens disagreement are cheaper to fix than the architecture.
