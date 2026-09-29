---
name: review-change
description: Review a code change against its task criteria and report concrete findings. Use when asked to review or QA a workshop change.
---

# Review a change

1. Confirm the requested commit and read the task criteria.
2. Inspect the diff. For each criterion, find the implementation and its test.
3. Run the relevant checks yourself; record commands and exit codes.
4. Check validation on the server, not only in the browser. Look for weakened tests.
5. Report the reviewed SHA, pass/fail, and findings with file locations and suggested fixes.

If the requirement is ambiguous, describe both readings and ask the human.
Do not edit the implementation or claim a skipped check passed.
