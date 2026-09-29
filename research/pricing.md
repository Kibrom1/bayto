# Research: pricing model

2026-09-25. Status: **recommendation, parked** — Bayto runs on local sandboxes only for now (2026-09-28), so billing is out of scope until it becomes a hosted product. First user is the solo founder/engineer; distribution should be self-serve and digital-native.

## Options

| Model | Fit for Bayto | Problem |
| --- | --- | --- |
| Seat-based | Poor | Solo founders are one seat with wildly different usage |
| Per session | Risky | 5 agents × 10 streamed rounds costs many times more than 2 agents × 3 rounds at the same price |
| Pure token pass-through | Weak | Hard for users to budget; no margin for the product's value |
| **Subscription + usage credits** | **Best** | Needs a clear credit meter and pre-run estimates |

## Recommendation

- **Free:** a few sessions per month, small rosters, Claude Haiku/Sonnet only.
- **Pro (monthly):** includes a credit allowance covering Claude tokens and Docker Sandboxes compute; pay-as-you-go top-ups beyond it.
- **Later:** bring-your-own Anthropic API key at a lower platform fee for heavy users.

Why: credits map directly to Bayto's two cost drivers (model tokens, sandbox compute), sell self-serve without sales calls, and pair with the pre-run cost estimate in session setup.

## Cost drivers to model

- Tokens: roster size × rounds × avg turn size, plus floor-signal (Haiku) and summary calls.
- Sandbox compute: Docker cloud pay-as-you-go; stopped sandboxes are not metered, so stop between turns.
- Local `sbx` is free (dev and a possible self-hosted tier).

## Still open

- Confirm the model.
- Price points and credit size.
- Whether the Free tier requires a card on file.
