# modes

A mode is data, not code (docs/agent-communication-protocol.md, 'Reuse'): `<name>.yaml` here
declares a floor policy, which Envelope kinds each role may send, an optional pipeline route
table, and an optional moderator role. Parsed by `acp.modes.load_mode` into a `ModeConfig`
(M2.5) -- see `agent-comms/tests/fixtures/modes/*.yaml` for the parsed shape and
`agent-comms/tests/test_modes.py` for the format's tests.

`open-chat.yaml`, `debate.yaml`, `brainstorm.yaml` (M2.11) are Bayto's three seeded discussion
modes -- all three use a `"*"` wildcard `roles` key (an open roster of ad hoc agent-template
instances, not a fixed team; see `acp.modes.ModeConfig.roles`'s docstring) and an optional
`synthesis_prompt_hint` (debate/brainstorm only) that steers the end-of-session Synthesizer's
system prompt without a new per-mode synthesizer class. `orchestrator/src/orchestrator/seed.py`
ensures a matching `Mode` DB row exists for each -- run it (`python -m orchestrator.seed`)
after editing any of these files so the DB row stays in sync; these YAML files themselves are
never round-tripped into the DB at session-run time (`modes_registry.resolve_mode` always
re-reads the file directly, per M2.5/M2.8's design -- see docs/decisions.md).

