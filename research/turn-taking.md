# Research: who speaks next?

2026-09-28. Question from Kibrom: does the moderator need to pick each speaker, or can any agent speak when it is addressed or has an idea?

## Answer

Self-selection is more natural and fits open chat and brainstorming, but a pure free-for-all breaks with LLM agents:

- **Everyone always has an opinion.** Asked "want to add something?", an LLM almost always says yes; cost multiplies and turns become restatement.
- **Collisions.** Several agents answering the same message produce overlapping, redundant turns.
- **Dominance.** Some personas dominate; quieter dissent gets drowned out.
- **No convergence.** A self-organizing group has no natural end.

## Recommended model: raise-hand

1. After each turn every agent returns a small signal: `wants_floor`, `reason` (addressed / new point / disagree / agree-pass), `urgency` 0–1.
2. The floor goes by rule: addressed (@mentioned) agent first; otherwise highest urgency, weighted up for new points and disagreement, down for agreement; speaking-time caps deprioritize heavy talkers.
3. "I agree" is a pass, not a turn (stops the echo chamber).
4. One objection per round can jump the queue.
5. No raised hands → converged → moderator summarizes or closes.

The moderator becomes a **referee**: breaks ties, invites quiet agents, enforces stop conditions, writes the synthesis.

| Mode | Turn-taking |
| --- | --- |
| Open chat, Brainstorm, Collaborate | Raise-hand (default) |
| Debate | Structured rounds (fairness between sides) |
| Delphi | Parallel and blind |
| Work teams (product-team build phase) | Pipeline routing |

## Architecture note

Even with all agents in one shared sandbox, asking every agent for its signal each turn would cost one Claude turn per agent. Compute all signals in **one Claude Haiku call** in the orchestrator using each persona plus the latest turn; only the winner gets an `assignment` and a wake-up. Trade-off: the signal doesn't see the agent's private task memory, which is acceptable for "should I speak?".

Formalized as the `FloorPolicy` interface in docs/agent-communication-protocol.md.
