"""A seat whose `claude` cannot run (bad model, not signed in inside the sandbox) used to leave the session `active`
with an empty error. The sandbox error now carries the reason, and the second failure in a row pauses the session
with that reason instead of waiting out two grant timeouts."""
import subprocess
import sys

from sqlalchemy import select

from orchestrator.floor import RoundRobinFloorPolicy
from orchestrator.models import Message as MessageRow
from orchestrator.models import Session
from orchestrator.sandbox.local import _LineStream
from orchestrator.sandbox.provider import SandboxCommandError, failure_cause

from fakes import FakeStreamingProvider
from test_local_fallback_runner import _setup, _turn


def test_failure_cause_prefers_stderr_then_the_result_event_then_the_last_lines():
    assert failure_cause("boom\n", '{"result": "x"}') == "boom"
    tail = '{"type":"system"}\n{"type":"result","is_error":true,"result":"There\'s an issue with the selected model."}'
    assert failure_cause("", tail) == "There's an issue with the selected model."
    assert failure_cause("", "plain line one\nplain line two") == "plain line one; plain line two"
    assert "printed nothing" in failure_cause("", "")


def test_the_command_error_message_says_why():
    err = SandboxCommandError(["exec"], 1, "", '{"result": "Not logged in"}')
    assert "Not logged in" in str(err) and err.cause == "Not logged in"


def test_a_stream_that_exits_1_with_empty_stderr_keeps_its_stdout_tail():
    script = "import sys; print('{\"type\":\"result\",\"is_error\":true,\"result\":\"bad model\"}'); sys.exit(1)"
    stream = _LineStream([sys.executable, "-c", script], ["exec"], "")
    try:
        for _ in stream:
            pass
    except SandboxCommandError as exc:
        assert exc.returncode == 1 and exc.cause == "bad model"
    else:
        raise AssertionError("expected SandboxCommandError")


class FailingStreaming(FakeStreamingProvider):
    async def run_turn(self, sandbox, role, prompt, *, env=None, model=None):
        self.prompts.append((role, prompt))
        raise SandboxCommandError(["exec", role], 1, "", '{"type":"result","result":"There\'s an issue with the selected model (x)."}')
        yield  # pragma: no cover  (makes this an async generator)


async def test_the_second_failure_in_a_row_pauses_the_session_with_the_reason(tmp_path, live_sessionmaker):
    session_id, runner, mirror, _ = await _setup(tmp_path, live_sessionmaker, [[]], fallback=False)
    provider = FailingStreaming([])
    runner._sandbox_provider = provider
    runner._stream_failures["a"] = (1, "earlier failure")  # the previous turn also failed
    await _turn(runner, mirror)

    assert provider.woken == []  # no waiting on the file path for a seat that cannot run
    await mirror.poll_once()
    async with live_sessionmaker() as db:
        row = await db.get(Session, session_id)
        msgs = (await db.execute(select(MessageRow).where(MessageRow.session_id == session_id))).scalars().all()
    assert row.status == "needs_human"
    assert any("could not run its turn 2 times in a row" in m.body and "selected model" in m.body for m in msgs)


def test_grpc_noise_on_stderr_does_not_hide_the_real_reason():
    from orchestrator.sandbox.provider import failure_cause
    noise = "I1027 22:47:03.867650 1541066 ev_poll_posix.cc:587] FD from fork parent still in poll list: fd(21, generation: 1)"
    tail = '{"type":"result","is_error":true,"result":"There\'s an issue with the selected model (claude-sonnet-5)."}'
    assert "selected model" in failure_cause(noise, tail)
    assert "no error output" in failure_cause(noise, "")
    assert failure_cause(noise + "\nbash: cd: /x: No such file or directory", tail) == "bash: cd: /x: No such file or directory"
