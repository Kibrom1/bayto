# Feature end-to-end test (no sandbox, no real models)

Runs the real Bayto app against real Postgres and a real browser, and fakes only what needs a Mac: the `sbx` sandbox
(a responder answers each woken seat through the real file transport and mirror) and the moderator's summary,
hand-raise and synthesis models. A local mock OpenAI server stands in for a provider, so the provider adapter makes real
HTTP calls and the test can read exactly what each seat was sent.

It checks, end to end: provider seats and model pickers, a long attachment added mid-session and retrieved per seat,
role-restricted attachments (never in another seat's prompt, nor in the final write-up), the live "Read:" notes, the
feedback link, an explicit stop with synthesis, and a provider rejecting its key (session pauses, key never shown), the room rail (mute, unmute, add and remove a seat), the ended-session bar, Start staying disabled until two seats are chosen, and the local-model fallback: the same mock server doubles as Ollama (`OLLAMA_HOST`), and a file `/tmp/e2e/claude_limit` (`<role or *> <reset epoch>`) makes the fake sandbox answer like Claude out of usage, so a turn is marked "Local model", the seat returns to Claude after the reset time, and a Build seat pauses instead.

```sh
createdb bayto_e2e && (cd orchestrator && alembic upgrade head && python -m orchestrator.seed)
export BAYTO_REPO=$PWD DATABASE_URL=postgresql+psycopg://bayto:bayto@localhost:5432/bayto_e2e PYTHONPATH=orchestrator/src \
  BAYTO_MODES_DIR=$PWD/modes BAYTO_EXTRA_MODELS="openai/mock:Mock GPT:small:0.01:0.1:0.2,ollama/llama3:Local Llama:small:0:0:0" OLLAMA_HOST=http://127.0.0.1:9100 OPENAI_API_KEY=sk-test-e2e \
  OPENAI_BASE_URL=http://127.0.0.1:9100/v1 BAYTO_FEEDBACK_URL=https://example.com/feedback E2E_SEAT_DELAY=6
python3 docs/e2e/features/mock_openai.py &                       # :9100, logs requests to /tmp/e2e/mock_openai.jsonl
(cd orchestrator && python ../docs/e2e/features/serve_with_fakes.py) &   # :8000
(cd web && npm run dev) &                                          # :5173
python3 docs/e2e/features/features_e2e.py                          # needs psycopg and playwright importable
```

Seats answer after `E2E_SEAT_DELAY` seconds so the test can add context while a debate is still running. Do not wait
for "network idle" in the browser: the room's live event stream never lets the network go idle.

Not covered: real `sbx` sandboxes, `claude -p`, a real OpenAI or Gemini endpoint, a real gateway, Build runs.
