# Per-agent models and providers: design, implementation plan and priorities

Status: proposal (2026-10-22). Nothing in this document is built yet except what the "What exists" section lists.

## Status (2026-10-23)

| Step | State |
| --- | --- |
| 1. Catalogue, `GET /models`, `invalid_model` on `POST /sessions`, model in the `session.created` audit roster | Done |
| 2. Seat model select in setup; estimate from each seat's model; model shown under each seat in the room | Done |
| 3. Default model on a custom agent (create, edit, dialog, audit) | Done |
| 4a. Per-session synthesis model (`synthesis_model` on the session, "Final write-up model" in setup) | Done |
| 4b. Provider adapter for discussion seats (OpenAI, Gemini, Ollama) | Done: see "Setting up a provider" below |
| 4c. Local model gateway (Route A), discussion seats only | Built, not run against a real gateway: see "Setting up a gateway" below |
| 4d. Build seats on a gateway model (the narrow form of Route C, priority P4) | Built behind `BAYTO_ALLOW_GATEWAY_BUILD=1`, off by default; tested with fakes only, never run against a real gateway, `sbx` or non-Claude model: see "Build seats on a gateway" below |

Estimate: each seat costs the usual per-seat figure ($0.50, or $3 for Build) times its model's `usd_per_turn` divided by the default model's, so Haiku is about 0.2x and Opus about 1.8x. The catalogue figures are planning numbers, not prices. `POST /sessions/{id}/continue` copies stored models without validating them, so older ids keep working. The seeded agents still carry the legacy `claude-sonnet-5`, shown as "Default (claude-sonnet-5)" in the seat select.

## Goal

Let a person choose which model, and later which provider, each agent uses: for example the Architect on a large model, the Tester on a small, cheap one, and (later) the Lawyer on a model that runs locally. Defaults stay as they are, so nobody has to choose.

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

## Providers (OpenAI, Gemini, others)

Today every agent turn runs through the `claude` command inside the sandbox, so only Claude models work. Choosing a provider means changing what runs a turn, not just a setting. Three routes:

| Route | What it is | Effort | Verdict |
| --- | --- | --- | --- |
| A. Gateway | An Anthropic-compatible proxy (LiteLLM, or similar) in front of OpenAI, Gemini or Ollama; a seat's `claude` process is pointed at it | Small | Fastest way to try other providers. Tool use is translated by the gateway, so quality varies. Same design as "Local models" above. |
| B. Provider adapter for discussion seats | A small interface in the orchestrator, one implementation per provider, used by discussion seats only | Medium | **Recommended.** Discussion turns are mostly text, so they do not need the agent tool loop. |
| C. Full multi-provider harness incl. Build | Replace the `claude`-based runner everywhere | Large | Not for the pilot. Build relies on the `claude` tool's permission model. |

### Route B in detail

```python
class Provider(Protocol):
    id: str                                   # "anthropic" | "openai" | "gemini" | "ollama"
    async def complete(self, *, model: str, system: str, messages: list[Msg],
                       max_tokens: int) -> Completion: ...   # text, tokens_in, tokens_out, cost

class Completion(BaseModel):
    text: str
    tokens_in: int
    tokens_out: int
    cost: float | None        # computed from the catalogue price; None when unknown
```

- **Where it plugs in.** The catalogue entry gains a `provider` field (default `anthropic`). A seat whose provider is `anthropic` runs exactly as today. A seat with any other provider is run by the orchestrator with `Provider.complete`, and its reply is written to the conversation log as that seat, so the moderator, floor policy, transcript, summary and cost ledger treat it like any other turn. Set `SessionAgent.harness` to the provider id; it is stored today and unused.
- **What these seats cannot do.** No file or shell tools. A discussion seat's read-only access to the project folder is replaced by the same text copy the attached-context feature already produces, so the seat is told about files as text. Build seats stay Anthropic-only; the setup page says so and the API refuses another provider for a Build seat (`422 provider_not_supported`).
- **Keys.** Read from environment variables (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `OLLAMA_HOST`) or a local secrets file outside the repository, never from the sandbox, never logged, never in an audit event. `GET /models` lists only providers whose key is present, and marks the others "needs a key" so the choice is visible but not selectable.
- **Failure.** A missing key, a rejected request or a timeout fails that turn with a plain message in the room. There is no fallback to another provider: a seat quietly answering from a different provider is a worse outcome than a visible failure.
- **Cost.** The catalogue holds a price per provider and model (input and output per million tokens). The estimate uses it; the dollar cap check works unchanged because turns record real cost. Your Claude subscription covers only Anthropic seats; other providers bill their own account, and the setup page says so.
- **Privacy.** A seat on another provider sends the conversation, and any attached context, to that provider. The setup page labels each seat with its provider, and a one-time notice appears the first time a non-Anthropic provider is chosen. The audit log records the provider per seat, never the key or the content.
- **Where the code goes.** `orchestrator/src/orchestrator/providers/` (one module per provider, a registry), the turn dispatch in `api/runtime.py` and the moderator's turn call, `GET /models` extended with provider and key status, the setup select grouped by provider.

### Tests for providers

- A fake provider for unit tests: returns canned text and token counts, to prove turns, cost and the cap check work for a non-Anthropic seat.
- Missing key, HTTP error and timeout each fail one turn with a clear message and leave the session able to continue.
- A Build seat with a non-Anthropic provider is refused.
- Secrets never appear in logs, audit events or API responses (a test greps the captured output for a sentinel key).
- Live, on a machine with real keys: one three-seat discussion mixing Anthropic and one other provider.

## Priorities

Ordered by value to the pilot against risk and effort. Items above the line are needed before or during the pilot; items below it are improvements that should wait for what the pilot teaches.

| # | Item | Why now | Effort | Depends on |
| --- | --- | --- | --- | --- |
| P0 | Real-run checks on a Mac: a real Build with a pull request, context added mid-session, an interrupted session continued | These are the only parts of the pilot never run against real models, GitHub or Docker | Small (manual) | none |
| P1 | Model catalogue, validation and `GET /models` (steps 1 and 2 of "Rollout order") | Stops typos failing mid-session; unlocks everything below; no migration | Small | none |
| P1 | Model select per seat in setup, per-seat cost estimate, model shown in the room | Delivers the request that started this document: Architect on a large model, Tester on a small one | Small to medium | the catalogue |
| P2 | Default model on a custom agent (dialog) | Convenience once seats can be set | Small | the catalogue |
| P2 | Provider adapter for discussion seats, with OpenAI and Gemini first (Route B) | Real choice of provider with controlled quality; discussion only | Medium | the catalogue, key handling |
| P3 | Gateway for local models (Route A), only if Route B does not cover the need | Local and self-hosted models | Small to medium | the catalogue |
| P3 | Per-session synthesis model | Large model writes the result while seats run small | Small | the catalogue |
| P4 | Providers for Build seats (Route C) | Only if pilot users ask; high risk | Large | everything above. The gateway form is built behind `BAYTO_ALLOW_GATEWAY_BUILD=1`; the full multi-provider harness is not. |

Reasoning: P0 comes first because every later item changes code that has only been exercised against a fake sandbox, and a failing Build or PR step would change the pilot plan. The catalogue is first among the code items because the picker, the estimate, the agent default and every provider all read it. Providers come after the picker because the picker alone covers the original request (Opus for the Architect, Haiku for the Tester) at much lower risk, and because the pilot will show whether people want other providers at all. Build stays Anthropic-only because its safety depends on the `claude` tool's permission model.

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

- Which providers matter to pilot users first: OpenAI, Gemini, a local model? Ask in the pilot feedback questions before building Route B.
- Is mixing providers in one discussion wanted, or would people rather pick one provider per session?

- Which model ids and tiers should be offered at launch, and what are their real per-turn costs? The figures above are planning numbers to be replaced with measured ones from real sessions.
- Should Build lock its roles to a minimum tier?
- Is a per-session synthesis model wanted, or is the global setting enough?


## Setting up a provider

Other providers are for discussion seats only. Build seats and the final write-up stay on Claude, and the server refuses (422 `provider_not_supported`) a Build roster or write-up that names another provider.

| Provider | Set on the orchestrator | Notes |
|---|---|---|
| OpenAI | `OPENAI_API_KEY`, optionally `OPENAI_BASE_URL` | Any OpenAI-compatible endpoint works through the base URL. |
| Google Gemini | `GEMINI_API_KEY` | |
| Ollama (local) | `OLLAMA_HOST`, e.g. `http://127.0.0.1:11434` | No key, no charge; cost shows as unknown. |

Models are listed with `BAYTO_EXTRA_MODELS`, comma separated: `id:label:tier:usd_per_turn[:price_in:price_out]`. The id is `<provider>/<model>`, for example `openai/gpt-4o-mini:GPT-4o mini:small:0.05:0.15:0.6`. The two optional prices are USD per million input and output tokens, used to record each turn's real cost. A model whose provider has no key shows in the picker as disabled with the variable it needs. Keys are read from the environment only, are removed from the environment of every program Bayto starts (`claude`, `sbx`, git, gh), and a provider error is shown without the request. `BAYTO_PROVIDER_TIMEOUT` (seconds, default 120) bounds each call.

A seat on another provider never falls back to a different model: if the call fails the session pauses for a human with the provider's message. Billing for these seats is on that provider's account, not your Claude subscription.

## Setting up a gateway

Route A: an Anthropic-compatible gateway (LiteLLM, or Ollama's Anthropic-compatible endpoint) sits in front of other models. A seat on a gateway model still runs `claude -p` in the sandbox, so it keeps its tools and read-only file access; only the endpoint its `claude` process talks to changes. **This was built and tested with fakes only (unit tests, a fake sandbox provider, and the sandbox turn script run with a stand-in `claude`). It has not been run against a real gateway, a real `sbx` sandbox or a real non-Claude model.** Treat tool use through a gateway as unproven until someone tries it.

| Set on the orchestrator | Meaning |
|---|---|
| `BAYTO_GATEWAY_URL` | The gateway's http(s) address **as the sandbox sees it** (for example `http://host.docker.internal:4000`), not `localhost`. Required. |
| `BAYTO_GATEWAY_TOKEN` | The key the gateway expects. Optional. Without one a placeholder is sent, never your Claude login. |
| `BAYTO_STREAMING_TURNS=1` | Required. A gateway seat runs as a headless streamed turn; the long-lived seat path has nowhere to put a per-seat environment. |

List each model in `BAYTO_EXTRA_MODELS` with the id `gateway/<name>`, where `<name>` is the model name the gateway knows (no `:`, which the list format uses as a separator): `gateway/qwen3:Qwen3 (gateway):small:0`. The picker shows it as "Qwen3 (Gateway)", disabled with the variable it needs until the gateway is set up. `GET /models` reports `provider: "gateway"`, `provider_label: "Gateway"`, `ready` and `needs`.

LiteLLM example (`config.yaml`), then `litellm --config config.yaml --port 4000`:

```yaml
model_list:
  - model_name: qwen3                 # the <name> in gateway/qwen3
    litellm_params:
      model: ollama_chat/qwen3        # any model LiteLLM supports
      api_base: http://127.0.0.1:11434
general_settings:
  master_key: sk-bayto-local          # then BAYTO_GATEWAY_TOKEN=sk-bayto-local
```

How it works, from the code:

- **What reaches the sandbox.** `LocalSbxSandboxProvider.run_turn` runs `sbx exec -i <sandbox> bash -lc <script> _ <role>`; the host's own environment does not reach the process inside, and the model comes from `team.tsv`. For a gateway seat the runner passes `env={ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN}` and `model=<name>` to `run_turn`, which runs `TURN_SCRIPT_OVERRIDE` instead: the values arrive on stdin ahead of the prompt (never in the command line, so not in `ps` or error text), only `ANTHROPIC_*` names are accepted, `ANTHROPIC_API_KEY` is unset first so a key already in the sandbox is not sent to the gateway, and `claude --model` gets the bare name. Every other seat calls `run_turn` exactly as before.
- **Secrets.** `child_env()` strips `BAYTO_GATEWAY_URL` and `BAYTO_GATEWAY_TOKEN` from everything the orchestrator starts (`sbx`, `dev-team/run.sh`, helper `claude` calls); the token goes only into the one seat's turn. It is not logged, not in an audit event (the roster records `gateway/<name>` only), and an error from that turn has the token redacted before it reaches the room.
- **Network.** Role files are written with the gateway host in `team_needs.network` for gateway seats only. The sandbox allow-list is the union over its seats, so the whole team's sandbox can then reach that host; the token still goes to the gateway seat's process alone.
- **No silent fallback.** A gateway seat is never woken through the long-lived seat path, never switched to the Ollama fallback after a platform-limit error, and never retried on another model. A missing gateway address, streamed turns switched off, or a failed turn pauses the session for a human with the reason.
- **Cost.** `claude` computes `total_cost_usd` from its own price table, which does not know gateway models, so the recorded cost may be wrong or zero. The catalogue's `usd_per_turn` is a planning figure only.

Why the final write-up stays on Claude (`422 provider_not_supported`, same as the other providers): it runs through the process-wide `llm/claude_cli.py`, which has no per-call environment. Build seats are covered in the next section.

### Build seats on a gateway (opt-in)

The earlier reason for refusing Build ("Build seats are long-lived processes with no per-seat environment") is only half right. `start-team` does start every seat as a long-lived process from `team.tsv`, and those have no per-seat environment. But the Build runner does not use them for turns when `BAYTO_STREAMING_TURNS=1`: the pipeline path in `moderator/runner.py` calls the same streamed `run_turn` as a discussion seat (`claude -p` started per turn from `TURN_SCRIPT`, reading the role's own tool-flag file), and only falls back to waking the long-lived seat when streaming is off. So the per-seat override already reaches a Build turn. What stopped Build was a refusal in the catalogue and `_gateway_model` returning nothing for pipeline sessions, not a missing mechanism.

To use it, set on the orchestrator, in addition to the gateway settings above:

| Set on the orchestrator | Meaning |
|---|---|
| `BAYTO_ALLOW_GATEWAY_BUILD=1` | Lets a Build seat use a `gateway/<name>` model. Anything but exactly `1` is off. Needs `BAYTO_GATEWAY_URL` and `BAYTO_STREAMING_TURNS=1` too. |

**Say this plainly before turning it on: tool use through a gateway varies a lot by model. A small or translated model may loop, call tools wrongly, ignore the `VERDICT:` line or write bad code, so expect more failed or paused Build runs than on Claude. Permissions are not weaker: `claude` itself still enforces the same `--allowedTools` / `--disallowedTools` under `--permission-mode dontAsk` for every Build seat, gateway or not. The gateway and the model never decide what a seat may run.** The setup page shows this note when a Build seat is on a gateway model.

What was done and what stays the same:

- **Off by default.** Without the flag, `POST /sessions` refuses a gateway model on a Build seat with `422 provider_not_supported` naming the flag, `GET /models` reports `build_ok: false` for it (so the picker hides it for Build seats), and the runner pauses a Build session that somehow holds one. `GET /models` has a new field `build_ok`: true for Claude models always, true for a gateway model only with the flag, false for every other provider.
- **Identical permissions.** The allow and deny rules come from `build_policy.build_seat_tools(role, ...)`, which never sees the model; `provision.write_catalog` writes them into the role file, and `build-roster.py` turns them into the tool-flag file that both `TURN_SCRIPT` and `TURN_SCRIPT_OVERRIDE` read. A test builds the four Build roles once with a Claude model and once with `gateway/qwen3` and asserts the rules, the CLI flags and the flag files are byte-identical (only `team.tsv`'s model differs), and runs both turn scripts with a stand-in `claude` and asserts the arguments match apart from `--model`.
- **Token.** Only the gateway seat's own turn gets `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` (stdin hand-over, never argv); a test runs a whole faked Build pipeline and asserts the other three seats get no override at all and the token is in no prompt, stored message or log line.
- **No fallback.** A gateway Build seat is never woken through the long-lived path, never moved to the Ollama fallback, never retried on another model. A gateway failure, a missing gateway address, streamed turns off or the flag switched off after the session was created pauses the session (`needs_human`) with the reason in the room.
- **Other providers.** OpenAI, Gemini and Ollama stay refused for Build (no tools): at create time, and now also at run time, where before a Build seat holding such an id would have been passed to `claude --model` unnoticed.
- **Final write-up.** Unchanged: Claude only.

Not verified, in addition to the list above: that a Build team starts at all when `team.tsv` names `gateway/<name>` for a seat (`start-team` launches that idle seat with `claude --model gateway/<name>` and no gateway environment, so it should fail or sit idle without ever being woken; if it makes `start-team` abort, the fix is to write a Claude model in `team.tsv` for gateway seats, since the streamed turn takes its model from the argument and not from that file); that a real model can follow the Build prompt and permission model through the gateway at all; that `git` commits made by a gateway developer behave like Claude's; and the cost recorded for the turn (see Cost above). The only thing run was fakes: a fake sandbox provider, a stand-in `claude`, and unit tests.

Not verified: that `sbx exec` passes stdin through to the script the way the stand-in test assumes (the existing streamed turns rely on it, so likely), that the gateway host is reachable from the sandbox with the allow-list written, what `start-team` does with an idle seat whose `team.tsv` model is `gateway/<name>`, and that tool calls survive the gateway's translation.
