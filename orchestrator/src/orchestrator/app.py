from fastapi import FastAPI

# Imported (not yet used) to prove the acp package is wired in as a real dependency
# (M1) rather than copy-pasted; the message mirror that actually reads Envelopes
# lands in M2.3.
from acp import Envelope  # noqa: F401

app = FastAPI(title="Bayto Orchestrator")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
