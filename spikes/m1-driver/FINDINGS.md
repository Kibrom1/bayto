# M1.10 floor-loop driver -- findings

Built and unit-tested against three fakes (`FakeSbxLifecycle`, `FakeHandRaiseScorer`,
`FakeSummarizer`); agent-comms' real `Conversation`/`FileTransport` is used directly (not
faked) for assignment send/tail/transcript, since it is already local file I/O with no
credential or network dependency. See `tests/`.

## The compound gap: no real `sbx` CLI and no real Anthropic API call, together

This sandbox has neither the host's `sbx` CLI (the same gap as M2.4 and the M1.10-M1.15
skip decision) nor a reachable `ANTHROPIC_API_KEY`/network egress (the same gap as M2.6's
`AnthropicHandRaiseScorer` and M2.7's `AnthropicSummarizer`/`AnthropicSynthesizer`). Those
two gaps have each been disclosed separately before; this spike is the first place they
compound: the floor loop's real end-to-end behavior -- a live `sbx env create`, a live
Haiku-scored hand-raise, a real `crew-notify` wake-up, a live moderator summary, a live
`sbx env rm` -- has not been exercised even once, start to finish, anywhere in this repo.

`SubprocessSbxLifecycle`'s argv shapes are extrapolated from the same
docs/product-design.md table M2.4 already extrapolates from (only `env create ...
--env-arg name=<name> --auto-approve` and `crew-notify <role>` are quoted verbatim there),
and `AnthropicHandRaiseScorer`/`AnthropicSummarizer` are verified only at the
parsing/validation level against canned tool_use fixtures (`tests/test_scorer.py`,
`tests/test_summarizer.py`).

Do not treat M1.10 as live-verified: it is built and tested against fakes per the
architect-confirmed spec (docs/decisions.md, 2026-10-04), not run against a real sandbox
or a real Anthropic call. That live run is M1.11-M1.16's job, on a host with `sbx` and
real credentials.

## Convergence rule

Deliberately simpler than M2's `RaiseHandFloorPolicy` (no queue-jump cap, no
`max_consecutive_grants`, no `ADDRESSED` short-circuit, no `ModeConfig`/`StopRulesConfig`):
each iteration, grant the floor to the single highest-urgency non-`AGREE_PASS` hand-raise;
converge once `K` consecutive iterations raise nothing but `AGREE_PASS` (or nothing at
all). This matches the scope the architect specified for this pre-M2 spike -- not a
simplification discovered along the way.

## Why no dependency on orchestrator/

`orchestrator/`'s `floor/types.py`, `floor/scorer.py` and `floor/raise_hand.py` only
import from `acp` and `pydantic` individually, but importing any of them pulls in the
`orchestrator` package as a whole, with its `pyproject.toml` dependency chain: FastAPI,
SQLAlchemy (async) + psycopg, Alembic, sse-starlette, OpenTelemetry. That is the wrong
weight for a spike meant to run as a standalone script against a single sandbox, pre-M2.
`spikes/m1-driver/` therefore defines its own spike-local `SbxLifecycle`,
`HandRaiseScorer` and `Summarizer` seams instead, each with a `Fake` for tests -- see
`docs/decisions.md`, 2026-10-04.
