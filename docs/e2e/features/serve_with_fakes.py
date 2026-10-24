#!/usr/bin/env python3
"""The real Bayto app with only the cloud-impossible parts faked: the sandbox (a responder that answers each woken seat
through the real FileTransport and mirror), the moderator's summariser, hand-raise scorer and synthesiser. Everything else
is the real code: Postgres, the runner, the floor policy, attachments, retrieval, provider adapters, the API and SSE.

  BAYTO_REPO=<checkout> DATABASE_URL=... python serve_with_fakes.py        # serves on :8000
"""
import asyncio, os, sys, time
from pathlib import Path

REPO = Path(os.environ["BAYTO_REPO"])
sys.path[:0] = [str(REPO / "orchestrator" / "tests"), str(REPO / "orchestrator" / "src"), str(REPO / "agent-comms" / "src")]
ROOT = Path(os.environ.get("BAYTO_FACTORY_ROOT", "/tmp/e2e/sessions")); ROOT.mkdir(parents=True, exist_ok=True)
os.environ["BAYTO_FACTORY_ROOT"] = str(ROOT)
SEAT_DELAY = float(os.environ.get("E2E_SEAT_DELAY", "1.2"))

from acp.models import Envelope
from acp.transport import FileTransport
from fakes import FakeSandboxProvider
from orchestrator.api.deps import get_hand_raise_scorer_factory, get_sandbox_provider, get_summarizer, get_synthesizer
from orchestrator.app import app
from orchestrator.moderator import FakeSummarizer, SummaryResult, SynthesisResult
from orchestrator.moderator.synthesizer import FakeSynthesizer


def _current_session_dir() -> Path:
    dirs = [d for d in ROOT.iterdir() if d.is_dir()]
    return max(dirs, key=lambda d: d.stat().st_mtime)


LIMIT_FILE = Path(os.environ.get("E2E_CLAUDE_LIMIT_FILE", "/tmp/e2e/claude_limit"))


def _limit_for(role: str) -> str | None:
    """While LIMIT_FILE holds '<role or *> <reset epoch>', a woken seat answers like Claude does when it is out of usage."""
    try: who, epoch = LIMIT_FILE.read_text().split()
    except (FileNotFoundError, ValueError): return None
    return epoch if who in ("*", role) else None


async def on_wake(role: str) -> None:
    await asyncio.sleep(SEAT_DELAY)  # slow enough that a test can add context mid-session
    d = _current_session_dir()
    epoch = _limit_for(role)
    if epoch:
        FileTransport(d).send(Envelope(conversation_id=d.name, from_=role, to=["moderator"], kind="answer",
                                        body=f"Claude AI usage limit reached|{epoch}", meta={}))
        return
    FileTransport(d).send(Envelope(conversation_id=d.name, from_=role, to=["moderator"], kind="answer",
                                    body=f"{role} here: I take a position and add one concrete point (turn at {time.time():.0f}).",
                                    meta={"tokens_in": 900, "tokens_out": 60, "cost": 0.004}))


class RecordingSynthesizer(FakeSynthesizer):
    """Writes the hint the write-up was given, so a test can check which attachments reached it."""
    async def synthesize(self, view, summary, all_turns, synthesis_prompt_hint=None, model=None):
        Path("/tmp/e2e/synth_hint.txt").write_text(synthesis_prompt_hint or "")
        return await super().synthesize(view, summary, all_turns, synthesis_prompt_hint=synthesis_prompt_hint, model=model)


sandbox = FakeSandboxProvider(on_wake=on_wake)
app.dependency_overrides[get_sandbox_provider] = lambda: sandbox
app.dependency_overrides[get_summarizer] = lambda: FakeSummarizer([SummaryResult(summary="running summary", has_new_argument=True) for _ in range(200)])
app.dependency_overrides[get_synthesizer] = lambda: RecordingSynthesizer(SynthesisResult(content_json={"decision": "E2E synthesis"}, source_turn_ids=[]))
app.dependency_overrides[get_hand_raise_scorer_factory] = lambda: (lambda: None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("API_PORT", "8000")), log_level="warning")
