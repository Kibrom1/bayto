#!/usr/bin/env bash
# Live check of the headless streaming turn (needs sbx + a running sandbox with a team).
# usage: bash spikes/m1-checks/m5-turn-live.sh SANDBOX_NAME [ROLE]
set -u
name="${1:?sandbox name}"; role="${2:-coordinator}"
out="$(printf 'Reply with exactly the word PONG.' | sbx exec -i "$name" bash -lc "$(python3 - <<'PY'
import re,sys
src=open('orchestrator/src/orchestrator/sandbox/local.py').read()
m=re.search(r'TURN_SCRIPT = r'''(.*?)'''',src,re.S)
print(m.group(1) if m else '')
PY
)" _ "$role" 2>&1)"
echo "$out" | head -c 1500
if echo "$out" | grep -q '"type":"result"'; then echo "PASS result event seen"; else echo "FAIL no result event"; fi
if echo "$out" | grep -q 'text_delta'; then echo "PASS partial text deltas streamed"; else echo "FAIL no text_delta"; fi
