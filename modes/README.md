# modes

A mode is data, not code (docs/agent-communication-protocol.md, 'Reuse'): `<name>.yaml` here
declares a floor policy, which Envelope kinds each role may send, an optional pipeline route
table, and an optional moderator role. Parsed by `acp.modes.load_mode` into a `ModeConfig`
(M2.5) -- see `agent-comms/tests/fixtures/modes/*.yaml` for the parsed shape and
`agent-comms/tests/test_modes.py` for the format's tests. No mode files are seeded here yet;
that's M2.11 (`open-chat`, `debate`, `brainstorm`).

