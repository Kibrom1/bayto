"""Subscription-backed stand-in for `AsyncAnthropic`, so the moderator needs no API key.

The summarizer, synthesizer and hand-raise scorer only ever call
`client.messages.create(model, max_tokens, system, messages, tools, tool_choice)` and read back a
`tool_use` block plus `usage`. `ClaudeCliClient` provides exactly that surface by running
`claude -p` (the same Claude Code login the sandboxed seats use), asking for a JSON object that
matches the forced tool's input schema, and wrapping the parsed object in a tool_use-shaped
response. The three classes take it through their existing `client=` parameter, unchanged.

ANTHROPIC_API_KEY is removed from the child's environment on purpose: if it were present the CLI
would bill the API key instead of using the subscription login.

UNVERIFIED against a live `claude` login until `spikes/m1-checks/moderator-cli-live.sh` is run.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any


class ClaudeCliError(RuntimeError):
    """The `claude` CLI failed, timed out, or returned something that is not the requested JSON."""


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _ToolUse:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class _Response:
    content: list[Any] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


def extract_json_object(text: str) -> dict:
    """Pull the first JSON object out of a model reply, tolerating code fences and chatter."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ClaudeCliError(f"no JSON object in the reply: {text[:200]!r}")
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ClaudeCliError(f"reply is not valid JSON ({exc}): {text[:200]!r}") from exc
    if not isinstance(obj, dict):
        raise ClaudeCliError("reply JSON is not an object")
    return obj


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))


class _Messages:
    def __init__(self, client: "ClaudeCliClient") -> None:
        self._c = client

    async def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict],
                     tools: list[dict], tool_choice: dict | None = None, **_: Any) -> _Response:
        tool = next((t for t in tools if not tool_choice or t["name"] == tool_choice.get("name")), tools[0])
        schema = json.dumps(tool["input_schema"])
        user = "\n\n".join(_text_of(m["content"]) for m in messages if m["role"] == "user")
        prompt = (
            f"{user}\n\n---\nRespond with ONLY one JSON object, no prose and no code fences, "
            f"that satisfies this JSON Schema (it is the input of the `{tool['name']}` tool):\n{schema}"
        )
        last: Exception | None = None
        for _attempt in range(2):
            out = await self._c.run(model=model, system=system, prompt=prompt)
            try:
                obj = extract_json_object(out["result"])
                return _Response([_ToolUse(tool["name"], obj)], _Usage(out["in"], out["out"]))
            except ClaudeCliError as exc:
                last = exc
        raise ClaudeCliError(f"{last} (after retry)")


class ClaudeCliClient:
    def __init__(self, *, binary: str | None = None, timeout: float = 240.0) -> None:
        self._binary = binary or os.environ.get("BAYTO_CLAUDE_BIN") or "claude"
        self._timeout = timeout
        self.messages = _Messages(self)

    async def run(self, *, model: str, system: str, prompt: str) -> dict:
        if shutil.which(self._binary) is None:
            raise ClaudeCliError(f"`{self._binary}` not found on PATH; install Claude Code and log in "
                                 "(or set BAYTO_CLAUDE_BIN)")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        argv = [self._binary, "-p", prompt, "--model", model, "--output-format", "json",
                "--append-system-prompt", system, "--max-turns", "1", "--tools", ""]
        # Run from an empty directory so no project CLAUDE.md or settings leak into the moderator.
        with tempfile.TemporaryDirectory(prefix="bayto-mod-") as cwd:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise ClaudeCliError(f"claude -p timed out after {self._timeout:.0f}s") from exc
        if proc.returncode != 0:
            raise ClaudeCliError(f"claude -p exited {proc.returncode}: {stderr.decode()[:300] or stdout.decode()[:300]}")
        try:
            env_json = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise ClaudeCliError(f"claude -p output was not JSON: {stdout.decode()[:200]!r}") from exc
        if env_json.get("is_error"):
            raise ClaudeCliError(f"claude -p reported an error: {str(env_json.get('result'))[:300]}")
        u = env_json.get("usage") or {}
        tokens_in = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) \
            + int(u.get("cache_creation_input_tokens", 0))
        return {"result": str(env_json.get("result", "")), "in": tokens_in, "out": int(u.get("output_tokens", 0))}
