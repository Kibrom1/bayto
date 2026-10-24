#!/usr/bin/env bash
# Run Claude Code (and optionally Bayto) against a local Ollama model instead of Anthropic.
#
#   scripts/bayto-local.sh check                 # is Ollama up, is the model there, does the Anthropic-style API answer
#   scripts/bayto-local.sh claude [args...]      # Claude Code in this terminal, on the local model
#   scripts/bayto-local.sh up                    # scripts/bayto-up.sh with the host-side `claude` on the local model
#
#   LOCAL_MODEL=qwen3-coder scripts/bayto-local.sh check      # default model: qwen3-coder
#   OLLAMA_URL=http://localhost:11434 ...                       # default shown
#   ALIAS_CLAUDE_NAMES=1 scripts/bayto-local.sh up              # also copy the model to the claude-* names Bayto asks for
#
# Needs: ollama (running), curl, and for `claude`/`up` the `claude` CLI. Nothing here changes your Claude login:
# the variables are set only for the command this script starts. Close that terminal and you are back on Anthropic.
#
# Limits (see https://docs.ollama.com/api/anthropic-compatibility): no tool_choice, prompt caching, token counting
# or PDFs, and local models are weaker than Claude. Bayto seats that run inside `sbx` sandboxes can only reach
# the hosts the sandbox allows, so they may not reach Ollama on your machine; `up` affects host-side calls
# (moderator, summaries, synthesis) for certain, and sandboxed seats only if your sandbox policy lets them reach it.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${LOCAL_MODEL:-qwen3-coder}"
URL="${OLLAMA_URL:-http://localhost:11434}"
cmd="${1:-check}"; shift || true

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; exit 2; }; }
need curl; need ollama

if ! curl -s -o /dev/null --max-time 3 "$URL/api/tags"; then
  echo "Ollama is not answering at $URL. Start it (open the Ollama app, or run: ollama serve) and retry."; exit 2
fi
if ! ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$MODEL\(:latest\)\?"; then
  echo "Model '$MODEL' is not pulled yet. Pull it now? (several GB) [y/N]"; read -r a
  [ "$a" = y ] || [ "$a" = Y ] || { echo "Skipped. Run: ollama pull $MODEL"; exit 2; }
  ollama pull "$MODEL" || exit 2
fi

if [ "${ALIAS_CLAUDE_NAMES:-0}" = 1 ]; then
  for n in claude-sonnet-5-5 claude-opus-5-5 claude-haiku-5-5; do
    ollama list | awk 'NR>1{print $1}' | grep -qx "$n\(:latest\)\?" || ollama cp "$MODEL" "$n" >/dev/null && echo "alias ready: $n -> $MODEL"
  done
fi

export ANTHROPIC_BASE_URL="$URL"
export ANTHROPIC_AUTH_TOKEN="ollama"
unset ANTHROPIC_API_KEY

probe() {
  echo "Testing the Anthropic-style API on $URL with $MODEL ..."
  out=$(curl -s --max-time 120 "$URL/v1/messages" -H "content-type: application/json" -H "x-api-key: ollama" \
        -H "anthropic-version: 2023-06-01" \
        -d "{\"model\":\"$MODEL\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: ready\"}]}")
  if echo "$out" | grep -qi '"text"'; then echo "OK: $(echo "$out" | head -c 300)"; return 0; fi
  echo "FAILED: $(echo "$out" | head -c 300)"; return 1
}

case "$cmd" in
  check)  probe ;;
  claude) need claude; probe || exit 1; echo "Starting Claude Code on $MODEL (local). Exit to return."; exec claude --model "$MODEL" "$@" ;;
  up)     need claude; probe || exit 1
          echo "Starting Bayto with the host-side Claude CLI on $URL. Seats in sbx sandboxes may not reach it."
          [ "${ALIAS_CLAUDE_NAMES:-0}" = 1 ] || echo "Tip: set ALIAS_CLAUDE_NAMES=1 so the claude-* model names Bayto sends resolve to $MODEL."
          exec "$REPO/scripts/bayto-up.sh" "$@" ;;
  *) echo "usage: $0 check|claude|up"; exit 2 ;;
esac
