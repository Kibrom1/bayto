#!/usr/bin/env python3
"""A stand-in for an OpenAI-compatible /v1/chat/completions endpoint. Logs every request (model, system, prompt) as a
JSON line so a test can see exactly what a provider seat was sent. Create the file named by MOCK_FAIL_FILE to make it
answer 401 like a bad key."""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("MOCK_LOG", "/tmp/e2e/mock_openai.jsonl")
FAIL = os.environ.get("MOCK_FAIL_FILE", "/tmp/e2e/mock_fail")
PORT = int(os.environ.get("MOCK_PORT", "9100"))
n = {"calls": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)) or 0) or b"{}")
        msgs = body.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        prompt = "\n".join(m["content"] for m in msgs if m["role"] == "user")
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": self.path, "auth": self.headers.get("authorization"), "model": body.get("model"),
                                "system": system, "prompt": prompt}) + "\n")
        time.sleep(0.4)
        if os.path.exists(FAIL):
            out, code = {"error": {"message": "Incorrect API key provided: mock"}}, 401
        else:
            n["calls"] += 1
            out, code = {"choices": [{"message": {"content": f"Mock GPT turn {n['calls']}: I weigh the evidence and add one new point."}}],
                         "usage": {"prompt_tokens": 1200, "completion_tokens": 40}}, 200
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
