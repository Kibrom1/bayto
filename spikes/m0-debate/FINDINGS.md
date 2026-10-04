# M0 debate spike -- findings

Two runs: an initial launch that failed entirely (vendor team.tsv's stale "claude-haiku" model alias, fixed in wad-103/PR #52), and a clean run afterward (run_id bayto-m0, attempt 1) across all 3 topics in spikes/m0-debate/topics/. Transcripts in spikes/m0-debate/runs/{01-microservices,02-ai-code,03-pricing}/ (note: each folder's cp swept the whole shared message store to that point, so segment by seq range per topic: topic 1 = seq 1-13, topic 2 = seq 14-26, topic 3 = seq 27-39).

## Biggest finding: the moderator's final summary never fires

roles/coordinator.md step 5 requires the moderator to send human one summary (strongest point per side, agreement, disagreement, recommendation) after round 3, then run `handoff stage finished`. Across all 3 topics, in both the interleaved first attempt and the clean second attempt (with 70-90s of idle time between topics), this never happened -- there is no coordinator -> human message anywhere in either run. This rules out a timing race; it is a reproducible gap in the moderator actually executing that step of its brief.

## Topic interleaving (first attempt only)

In the first (messy) attempt, a new topic was asked via `crew ask` before the previous topic finished its 3 rounds. This caused real corruption: topic 2's round 2 reply arrived after topic 3's question had already been sent, and the coordinator jumped to assigning topic 3 without topic 2 ever reaching round 3. The clean second attempt, with ~70-90s pauses between topics, had no interleaving -- waiting for the previous topic's round 3 exchange to be visibly complete in `crew watch` before the next `crew ask` avoids this.

## Word limits

Mostly held. Developer crept over the round-3/round-2 rebuttal cap (120 words) with an increasing trend across topics: topic 2 round 3 (123w), topic 3 round 2 (129w), topic 3 round 3 (135w). qa stayed under its caps except one 155-word opening reply in topic 1 (cap 150w). No instance was severe enough to call dominance, but the trend across topics is worth watching if this spike's mechanics get reused.

## Echo

None observed. Every reply across all 3 topics and both attempts was substantively distinct from prior turns.

## Wake-up reliability

The clean run had no stalls or resends -- messages flowed at roughly 4-15 second intervals throughout. The only wake-up/start failure observed was the first attempt's total failure to start any seat at all, caused by the stale model alias (environment bug, not a crew-notify/wake-up protocol issue; already fixed separately).

## Were all 3 rounds enforced?

Yes, in the clean run: every topic got an opening exchange, a correctly labeled "Round 2/3", and a correctly labeled "Round 3/3 (final)" round before cutting off (at the missing-summary gap noted above).
