"""Subscription-backed stand-in for `AsyncAnthropic`, so the moderator needs no API key.

The summarizer, synthesizer and hand-raise scorer only ever call
`client.messages.create(model, max_tokens, system, messages, tools, tool_choice)` and read back a
`tool_use` block plus `usage`. `ClaudeCliClient` provides exactly that surface by running
`claude -p` (the same Claude Code login the sandboxed seats use), asking for a JSON object that
matches the forced tool's input schema, and wrapping the parsed object in a tool_use-shaped
response. The three classes take it through their existing `client=` parameter, unchanged.

ANTHROPIC_API_KEY is removed from the child's environment on purpose: if it were present the CLI
would bill the API key instead of using the subscription login.

Verified live 2026-10-18: real Build sessions (docs/e2e/live/live_build_e2e.py, BAYTO_LLM_BACKEND=cli) produced a
synthesis artifact through this backend on a Claude subscription login.
"""
from __future__ import annotations

import asyncio
import json
import os

from ..child_env import child_env
from ..limits import LimitHit, detect_limit, detect_limit_in_text
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any


class ClaudeCliError(RuntimeError):
    """The `claude` CLI failed, timed out, or returned something that is not the requested JSON.
    `limit` is set when the CLI said the account is out of usage or credit (see limits.py)."""

    def __init__(self, message: str = "", *, limit: LimitHit | None = None) -> None:
        super().__init__(message)
        self.limit = limit


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


_JSON_TYPES = {"string": str, "boolean": bool, "object": dict, "array": list, "null": type(None),
               "integer": int, "number": (int, float)}


def _type_ok(value: Any, name: Any) -> bool:
    py = _JSON_TYPES.get(name) if isinstance(name, str) else None
    if py is None:
        return True  # a type name we do not know is not a reason to reject the reply
    if isinstance(value, bool):
        return name in ("boolean",)
    return isinstance(value, py)


def schema_errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Check `value` against the subset of JSON Schema the tool schemas use (type, required, properties,
    items, enum, anyOf/oneOf). A reply that is valid JSON but the wrong shape used to flow on into the
    runner and fail far from its cause (a KeyError, or a number where text was expected); catch it here
    instead, where it can be retried with the problem spelled out. `type` may be a list
    (`["object", "null"]`). Keywords not handled here are ignored, never an error."""
    errors: list[str] = []
    want = schema.get("type")
    if want is not None:
        names = want if isinstance(want, list) else [want]
        if not any(_type_ok(value, n) for n in names):
            label = "/".join(str(n) for n in names)
            return [f"{path}: expected {label}, got {type(value).__name__}"]
    for key in ("anyOf", "oneOf"):
        alts = schema.get(key)
        if isinstance(alts, list) and alts and all(schema_errors(value, a, path) for a in alts if isinstance(a, dict)):
            return [f"{path}: does not match any allowed shape"]
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}: missing required field '{key}'")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value and isinstance(sub, dict):
                errors += schema_errors(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{path}[{i}]")
    return errors


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
        attempt_prompt = prompt
        for _attempt in range(2):
            out = await self._c.run(model=model, system=system, prompt=attempt_prompt)
            try:
                obj = extract_json_object(out["result"])
                problems = schema_errors(obj, tool["input_schema"])
                if problems:
                    raise ClaudeCliError("reply does not match the schema: " + "; ".join(problems[:5]))
                return _Response([_ToolUse(tool["name"], obj)], _Usage(out["in"], out["out"]))
            except ClaudeCliError as exc:
                last = exc
                # Say what was wrong, so the retry is not just the same request again.
                attempt_prompt = f"{prompt}\n\nYour previous reply was rejected: {exc}\nReply again with a corrected JSON object."
        raise ClaudeCliError(f"{last} (after retry)")


def failure_detail(stdout: str, stderr: str) -> str:
    """Why `claude -p` failed, in words. With --output-format json the reason (not logged in, unknown
    model, rate limit...) is in the JSON `result`, while the first 300 characters of that JSON are only
    bookkeeping (cost, usage), so a plain slice of stdout hid it."""
    err = stderr.strip()
    if err:
        return err[:400]
    try:
        env = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout.strip()[:400]
    if isinstance(env, dict):
        for key in ("result", "error", "message"):
            if env.get(key):
                return str(env[key])[:400]
        if env.get("subtype"):
            return f"subtype={env['subtype']}"
    return stdout.strip()[:400]


class ClaudeCliClient:
    def __init__(self, *, binary: str | None = None, timeout: float = 240.0) -> None:
        self._binary = binary or os.environ.get("BAYTO_CLAUDE_BIN") or "claude"
        self._timeout = timeout
        self.messages = _Messages(self)

    async def run(self, *, model: str, system: str, prompt: str) -> dict:
        if shutil.which(self._binary) is None:
            raise ClaudeCliError(f"`{self._binary}` not found on PATH; install Claude Code and log in "
                                 "(or set BAYTO_CLAUDE_BIN)")
        env = {k: v for k, v in child_env().items() if k != "ANTHROPIC_API_KEY"}
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
            detail = failure_detail(stdout.decode(), stderr.decode())
            try:
                structured = json.loads(stdout.decode())
            except json.JSONDecodeError:
                structured = None
            limit = detect_limit(structured) or detect_limit_in_text(detail)
            raise ClaudeCliError(f"claude -p exited {proc.returncode}: {detail}", limit=limit)
        try:
            env_json = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise ClaudeCliError(f"claude -p output was not JSON: {stdout.decode()[:200]!r}") from exc
        if env_json.get("is_error"):
            raise ClaudeCliError(f"claude -p reported an error: {str(env_json.get('result'))[:300]}",
                                 limit=detect_limit(env_json))
        u = env_json.get("usage") or {}
        tokens_in = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) \
            + int(u.get("cache_creation_input_tokens", 0))
        return {"result": str(env_json.get("result", "")), "in": tokens_in, "out": int(u.get("output_tokens", 0))}


_LOGIN_OK_UNTIL = 0.0


async def check_login(model: str | None = None, *, client: "ClaudeCliClient | None" = None,
                      cache_seconds: float = 120.0) -> None:
    """Fail fast when the `claude` login the moderator runs on is missing or expired: one tiny call,
    cached for a couple of minutes so Start/Resume do not pay for it every time. Raises ClaudeCliError
    with the CLI's own reason (for example "OAuth session expired")."""
    global _LOGIN_OK_UNTIL
    now = time.monotonic()
    if now < _LOGIN_OK_UNTIL:
        return
    cli = client or ClaudeCliClient(timeout=60.0)
    await cli.run(model=model or os.environ.get("BAYTO_SUMMARY_MODEL") or "claude-sonnet-5-5",
                  system="Reply with the single word: ok", prompt="ping")
    _LOGIN_OK_UNTIL = now + cache_seconds


def reset_login_cache() -> None:
    global _LOGIN_OK_UNTIL
    _LOGIN_OK_UNTIL = 0.0
