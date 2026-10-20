"""Runs the real Bayto app with a fake sandbox: scripted agent replies, no sbx, no model. For usability testing only."""
import asyncio, os, subprocess, sys, itertools
from pathlib import Path

sys.path[:0] = ["/home/claude/kibrom1/bayto/orchestrator/src", "/home/claude/kibrom1/bayto/orchestrator/tests",
                "/home/claude/kibrom1/bayto/agent-comms/src"]
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://bayto:bayto@localhost:5432/bayto_ux")
os.environ["BAYTO_FACTORY_ROOT"] = "/tmp/tr/harness/sessions"
os.environ["BAYTO_MODES_DIR"] = "/home/claude/kibrom1/bayto/modes"
DELAY = float(os.environ.get("FAKE_DELAY", "1.5"))

from acp.models import Envelope
from acp.transport import FileTransport
import uvicorn
from fakes import FakeSandboxProvider
from orchestrator.app import app
from orchestrator.floor.scorer import FakeHandRaiseScorer
from orchestrator.moderator import SummaryResult, SynthesisResult

LINES = {
 "architect": ["I'd go weekly. Smaller batches mean a smaller blast radius, and our CI already runs in nine minutes.",
               "Biweekly only helps if review is the bottleneck. Is it? I haven't seen evidence either way.",
               "Agreed on a hotfix lane. I'd still trial weekly for one quarter and measure."],
 "pm": ["Customers ask for predictability more than speed. Biweekly with a hotfix lane gives both.",
        "Weekly risks release fatigue and thinner release notes; people stop reading them.",
        "A trial with a clear review date is fine, if support agrees to the notes format."],
 "security-reviewer": ["Weekly shortens the window between a fix and its release, which I like, but review time shrinks too.",
                       "I'd want a standing security checklist before agreeing to either cadence.",
                       "A checklist plus the hotfix lane covers my concerns."],
 "lawyer": ["Release notes are a contractual surface for two of our customers. Cadence must not shorten notice periods.",
            "A weekly cadence is fine if the notice rule is unchanged.", "No objection to the trial."],
 "customer": ["I want to know what changed and when. Fewer surprises matter more than speed.",
              "A hotfix lane is good. Notes in plain language, please.", "Trial is fine."],
 "historian": ["Teams that moved to weekly often moved back within a year when review debt built up.",
               "The ones that kept it had a named release owner each week.", "Name the owner, then trial."],
}
counter = itertools.count()
state = {"blocked_once": set(), "workspace": None}

def reply_for(role, sid):
    if role in LINES:
        n = sum(1 for r in state.setdefault(sid, []) if r == role)
        state[sid].append(role)
        return LINES[role][min(n, 2)]
    return f"{role} response"

async def on_wake(role):
    sid = on_wake.session_id()
    await asyncio.sleep(DELAY)
    body = None
    ws = state["workspace"]
    if role.startswith("build-"):
        brief = on_wake.brief()
        if role == "build-architect":
            if "[block]" in brief and sid not in state["blocked_once"]:
                state["blocked_once"].add(sid)
                body = "I can't plan this: the brief doesn't say what to change. What should the change be?\n\nVERDICT: blocked"
            else:
                body = "Plan: add hello() to hello.py and a test in test_hello.py. Acceptance: pytest passes.\n\nVERDICT: planned"
        elif role == "build-developer":
            if ws:
                Path(ws, "hello.py").write_text("def hello():\n    return 'hello'\n")
                Path(ws, "test_hello.py").write_text("from hello import hello\n\ndef test_hello():\n    assert hello() == 'hello'\n")
                subprocess.run(["git", "-C", ws, "add", "-A"], check=True)
                subprocess.run(["git", "-C", ws, "-c", "user.name=dev", "-c", "user.email=d@x", "commit", "-qm", "Add hello()"], check=True)
            body = "Added hello() and its test.\n\nVERDICT: implemented"
        elif role == "build-tester":
            body = "Ran the suite:\n```\n$ pytest -q\n1 passed\n```\nexit code 0\n\nVERDICT: pass"
        else:
            body = "The diff is small and readable. Approved.\n\nVERDICT: approve"
    else:
        body = reply_for(role, sid)
    n = next(counter)
    FileTransport(Path(os.environ["BAYTO_FACTORY_ROOT"]) / str(sid)).send(Envelope(
        conversation_id=str(sid), from_=role, to=["moderator"], kind="answer", body=body,
        meta={"tokens_in": 3000, "tokens_out": 350, "cost": 0.12}))

class Provider(FakeSandboxProvider):
    async def create(self, task_id, *, name, seats=None, workspace=None):
        state["workspace"] = workspace
        return await super().create(task_id, name=name, seats=seats, workspace=workspace)

provider = Provider(on_wake=on_wake)
# the runner tells us which session a wake belongs to: read the newest session folder
def _latest():
    root = Path(os.environ["BAYTO_FACTORY_ROOT"])
    dirs = [d for d in root.glob("*") if d.is_dir()]
    return max(dirs, key=lambda d: d.stat().st_mtime).name if dirs else None
on_wake.session_id = _latest
def _brief():
    import psycopg
    return ""
on_wake.brief = lambda: Path("/tmp/tr/harness/current_brief.txt").read_text() if Path("/tmp/tr/harness/current_brief.txt").exists() else ""

class Summ:
    async def summarize(self, session_id, previous_summary, new_turns):
        return SummaryResult(summary="Summary: " + " | ".join(t.content[:70] for t in new_turns[-3:]), has_new_argument=True)
class Synth:
    async def synthesize(self, view, summary, all_turns, synthesis_prompt_hint=None):
        return SynthesisResult(content_json={
            "summary": "The group leaned towards a biweekly cadence with a hotfix lane, to be reviewed after one quarter.",
            "decision": "Go biweekly with a hotfix lane and revisit in a quarter.",
            "key_points": ["Predictability matters more to customers than speed.", "A hotfix lane covers urgent fixes.", "Name a release owner each cycle."],
            "next_steps": ["Write the release-notes format", "Set the review date"]},
            source_turn_ids=[], minority_report={"position": "Architect would still trial weekly for one quarter and measure."})

app.state.sandbox_provider_factory = lambda: provider
app.state.summarizer_factory = lambda: Summ()
app.state.synthesizer_factory = lambda: Synth()
app.state.scorer_factory = lambda: FakeHandRaiseScorer([])
async def _ok(): return None
app.state.login_check = _ok
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
