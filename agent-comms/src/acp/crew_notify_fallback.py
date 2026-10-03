#!/usr/bin/env python3
import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime, timezone

# Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "llama3")
FACTORY_DIR = Path(os.getenv("FACTORY_DIR", "/factory"))

def main():
    if len(sys.argv) < 2:
        print("Usage: crew-notify-fallback <role>")
        sys.exit(1)

    role = sys.argv[1]

    # 1. Find the latest assignment for this role
    # Assignments are in the factory directory as .json or .txt files (ACP format)
    # We'll look for the most recent file that is an 'assignment' kind and 'to' the role.

    last_assignment = None
    last_seq = -1

    # In ACP, messages are often stored in a 'messages' folder or just in the root.
    # We'll scan for all files and try to parse them as Envelopes.
    for path in FACTORY_DIR.rglob("*.json"):
        try:
            with open(path) as f:
                env = json.load(f)
                if env.get("kind") == "assignment" and role in env.get("to", []):
                    seq = env.get("seq", 0)
                    if seq > last_seq:
                        last_seq = seq
                        last_assignment = env
        except Exception:
            continue

    if not last_assignment:
        print(f"No assignment found for role {role}")
        sys.exit(0)

    prompt = last_assignment.get("body", "Please respond to the current assignment.")

    # 2. Call Ollama
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": FALLBACK_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )
        response.raise_for_status()
        text = response.json().get("response", "")
    except Exception as e:
        print(f"Ollama error: {e}")
        sys.exit(1)

    # 3. Write back the response as an ACP Envelope
    # We need a unique message ID and the next sequence number.

    # Find the current max seq
    max_seq = 0
    for path in FACTORY_DIR.rglob("*.json"):
        try:
            with open(path) as f:
                env = json.load(f)
                max_seq = max(max_seq, env.get("seq", 0))
        except Exception:
            continue

    envelope = {
        "message_id": f"fallback-{datetime.now(timezone.utc).timestamp()}",
        "from": role,
        "to": ["*"],
        "kind": "report",
        "seq": max_seq + 1,
        "body": text,
        "meta": {
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0,
            "fallback": True
        }
    }

    # Save to factory dir
    out_path = FACTORY_DIR / f"msg_{envelope['seq']}_{role}.json"
    with open(out_path, "w") as f:
        json.dump(envelope, f)

    print(f"Fallback response written to {out_path}")

if __name__ == "__main__":
    main()
