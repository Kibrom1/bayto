# Per-agent models: design and implementation plan

Status: proposal (2026-10-22). Nothing in this document is built yet except what the "What exists" section lists.

## Goal

Let a person choose which model each agent uses: for example the Architect on a large model, the Tester on a small, cheap one, and (later) the Lawyer on a model that runs locally. Defaults stay as they are, so nobody has to choose.

## What exists today

| Piece | Where | State |
| --- | --- | --- |
| A model on the agent | `Agent.model` (`models.py`) | Set once. Seeded agents use `AGENT_MODEL` (`seed.py`); custom agents are created with the same constant (`api/agents.py`). Not editable. |
| A model on the seat | `SessionAgent.model`, `RosterEntry.model` in `POST /sessions` (`api/sessions.py`) | Stored, free text (up to `LABEL_MAX` characters), never validated. `POST /sessions/{id}/continue` copies it. |
| Resolution | `api/runtime.py`: `model=sa.model or a.model` | The seat's model wins over the agent's. |
| Reaching the model | `sandbox/provision.py` writes `"<role> <model>"` into the roster file (`team.tsv`); `sandbox/local.py` `TURN_SCRIPT` reads the role's model and runs `claude -p --model "$model"` | Works for any model name the `claude` command accepts. |
| Helper models | `BAYTO_SUMMARY_MODEL`, `BAYTO_SYNTHESIS_MODEL`, `BAYTO_HAND_RAISE_MODEL` (`scripts/bayto-up.sh`, `llm/claude_cli.py`) | Global per process, not per agent. |
| `SessionAgent.harness` | stored from the request | Nothing reads it. |
| UI | `web/src/api/sessions.ts` has `model?` on the roster entry | The setup page and the agent dialog expose no model choice. |

So a model per seat already works if you call the API by hand. The work is making it safe and usable.

## Design

### 1. A catalogue of allowed models (server)

A small module, `orchestrator/src/orchestrator/models_catalog.py`, is the single list of models a person may choose.

```python
@dataclass(frozen=True)
class ModelInfo:
    id: str            # what is passed to `claude --model`
    label: str         # "Opus 5.5"
    tier: str          # "large" | "standard" | "small"
    usd_per_turn: float  # rough planning figure for a typical discussion turn
    default: bool = False
```

- Initial entries: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`. Confirm the exact ids against `claude --help` or the API docs before shipping; the seed currently uses `claude-sonnet-5`.
- The list can be extended without code through `BAYTO_EXTRA_MODELS` (comma-separated `id:label:tier:usd_per_turn`), which is also how a gateway model (see "Local models") gets listed.
- `GET /models` returns the catalogue and the default id. The web app reads it once.

### 2. Validation (server)

- `POST /sessions` and `POST /sessions/{id}/continue`: a seat `model` must be `null` or a catalogue id, otherwise `422 invalid_model` with the field `roster.model`. Today a typo only fails when that seat first speaks.
- Agent create and update (`api/agents.py`): add an optional `model` field (`null` means "use the default"), validated the same way. Custom agents then carry their own default, and a seat can still override it.
- Existing rows are left alone. A stored model that is not in the catalogue (for example the legacy `claude-sonnet-5`) is treated as valid when read, so old sessions and seeded agents keep working; only new writes are checked.
- Build mode: the pipeline roles (architect, developer, tester, reviewer) take the same per-seat model through `team.tsv`. Allow it, but default to the standard tier, and show a note that a small model on the developer seat may fail more tests.

### 3. UI

- **Session setup** (`web/src/pages/SessionSetup.tsx`): each seat in "Selected roster" gets a model select, "Default (Sonnet 5.5)" first, then the catalogue. The choice is sent as `roster[].model`. A changed seat shows its model name next to the agent so the setup is readable at a glance.
- **Agent dialog** (`components/AgentDialog.tsx`): an optional "Default model" select. Editing it changes future sessions only.
- **Room** (`pages/BaytoRoom.tsx`): the roster panel shows each seat's model under its stance, so a reader of a transcript can tell why the Tester sounds different.
- **Continue after restart** already copies `SessionAgent.model`, so it needs no UI.

### 4. Cost estimate

`SessionSetup.tsx` uses one flat `COST_PER_SEAT = 0.5` (and `3` for Build). Replace it with the sum of each seat's `usd_per_turn` times the expected turns per seat (the current constants imply about 6 turns, so keep that as a named constant). Because the dollar cap is checked before each turn using the average cost so far (see `docs/decisions.md`, 2026-10-22), a cheap seat lowers the average and a large one raises it, with no change to the runner. The estimate stays labelled "a planning estimate, not a quote".

### 5. Helper models

Summary, synthesis and hand-raise use process-wide settings. Leave them as they are for the first release. A later option is a per-session "synthesis model" so a debate can be written up by the largest model while the seats run on smaller ones.

### 6. Audit

`session.created` already logs the roster; add each seat's resolved model to its `roster` detail so a session's models are visible in the audit trail. Agent create and update events add `model`.

## Local models

Not supported today, and not a small change, because every turn runs through the `claude` command, which talks to Anthropic's API.

One workable route is an Anthropic-compatible gateway in front of the local model (for example LiteLLM in front of Ollama):

1. Run the gateway on the host and register the local model under an id such as `local-llama`.
2. Add it to the catalogue through `BAYTO_EXTRA_MODELS`, with the tier `local` and cost `0`.
3. Point that seat's `claude` process at the gateway: the turn script would export `ANTHROPIC_BASE_URL` (and a token) for roles whose model is flagged as gateway-backed. This needs a per-model "endpoint" in the catalogue and a change to `TURN_SCRIPT` in `sandbox/local.py`.
4. Allow the sandbox to reach the gateway. Roles currently declare `team_needs.network: ["api.anthropic.com"]` in `sandbox/provision.py`; a gateway host would be added for those roles only.
5. Use `SessionAgent.harness` (stored, unused today) as the switch, for example `harness = "gateway"`.

Risks to test before promising it: small local models are weak at the tool use the Build roles need, so limit local models to discussion seats at first; latency and context limits differ; and a seat that quietly falls back to a hosted model would defeat the reason for choosing a local one, so a gateway failure should fail the turn loudly.

## Rollout order

1. Catalogue, `GET /models`, validation, audit detail (backend, with tests).
2. Seat model select in setup, estimate from per-seat cost, room roster display.
3. Agent default model in the dialog.
4. Local model gateway, only if the earlier steps prove useful.

Steps 1 to 3 are roughly one branch of work and add no migration: `Agent.model` and `SessionAgent.model` already exist.

## Tests

- Backend: valid and invalid seat models on create and continue; the seat model overrides the agent's; legacy ids still load; `GET /models` shape; audit detail contains the resolved model.
- Web: the select lists the catalogue, sends `roster[].model`, the estimate changes with the chosen models, the room shows the model.
- Live (on a machine with `claude`): one discussion with three different models, checking the model names in `team.tsv` and that each turn's reported model matches.

## Open questions

- Which model ids and tiers should be offered at launch, and what are their real per-turn costs? The figures above are planning numbers to be replaced with measured ones from real sessions.
- Should Build lock its roles to a minimum tier?
- Is a per-session synthesis model wanted, or is the global setting enough?
