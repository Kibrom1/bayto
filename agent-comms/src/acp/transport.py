"""Transport SPI and the file-based transport (workshop-compatible in spirit)."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from .models import BROADCAST, Envelope


class Transport(Protocol):
    def send(self, env: Envelope) -> Envelope: ...
    def read(self, recipient: str, *, attempt: int | None = None) -> list[Envelope]: ...
    def ack(self, recipient: str, message_id: str) -> None: ...
    def all(self) -> list[Envelope]: ...


def _atomic_write(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)  # temp+rename: safe on virtiofs mounts
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class FileTransport:
    """Messages are JSON files: <root>/messages/<seq:08d>-<id>.json.

    Consumption is per recipient: <root>/consumed/<recipient>/<message_id>.
    A global seq is assigned under an flock, so concurrent senders are ordered.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.msgs = self.root / "messages"
        self.consumed = self.root / "consumed"
        self.msgs.mkdir(parents=True, exist_ok=True)
        self.consumed.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.root / ".seq.lock"
        self._seq_path = self.root / ".seq"

    def send(self, env: Envelope) -> Envelope:
        with open(self._lock_path, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                seq = int(self._seq_path.read_text()) + 1 if self._seq_path.exists() else 1
                env = env.model_copy(update={"seq": seq})
                _atomic_write(self.msgs / f"{seq:08d}-{env.message_id}.json", json.dumps(env.dump()))
                _atomic_write(self._seq_path, str(seq))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return env

    def all(self) -> list[Envelope]:
        out = []
        for p in sorted(self.msgs.glob("*.json")):
            out.append(Envelope.model_validate(json.loads(p.read_text())))
        return out

    def messages_after(self, seq: int) -> list[Envelope]:
        """All messages with seq > `seq`, in seq order. For a watcher that mirrors this
        transport elsewhere (e.g. the orchestrator's M2.3 Postgres mirror) and wants to
        resume from the last one it saw, without duplicating `all()`'s parsing."""
        return [e for e in self.all() if e.seq is not None and e.seq > seq]

    def _is_consumed(self, recipient: str, message_id: str) -> bool:
        return (self.consumed / recipient / message_id).exists()

    def read(self, recipient: str, *, attempt: int | None = None) -> list[Envelope]:
        """Unconsumed messages for recipient (direct or broadcast, not own sends)."""
        res = []
        for env in self.all():
            if env.from_ == recipient:
                continue
            if recipient not in env.to and BROADCAST not in env.to:
                continue
            if attempt is not None and env.attempt != attempt:
                continue  # stale attempt
            if self._is_consumed(recipient, env.message_id):
                continue
            res.append(env)
        return res

    def ack(self, recipient: str, message_id: str) -> None:
        d = self.consumed / recipient
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / message_id, "1")

    def claim(self, name: str) -> bool:
        """Single-fire claim via mkdir (atomic)."""
        try:
            (self.root / "claims").mkdir(exist_ok=True)
            (self.root / "claims" / name).mkdir()
            return True
        except FileExistsError:
            return False

    # --- reports (ack != report) ---
    def report(self, role: str, message_id: str, status: str, checks: dict[str, int] | None = None,
               summary: str = "", stage: str | None = None, output_ref: str | None = None) -> dict:
        """Record a result for a message. checks maps command -> real exit code.

        `status` is an open string; see acp.models.CANONICAL_STATUSES for the doc's
        example terminal states (implemented, review-pass, ...). "pass" is special-cased
        below as a convenience convention, not because the vocabulary is closed.
        `verified` is vacuously true when `checks` is empty (no checks run, nothing
        failed) -- review discipline, not this flag, is the backstop against empty-check
        reports of a passing status.
        """
        d = self.root / "reports"
        d.mkdir(exist_ok=True)
        checks = checks or {}
        rec = {"from": role, "message_id": message_id, "status": status,
               "stage": stage, "output_ref": output_ref,
               "checks": checks, "summary": summary,
               "verified": all(v == 0 for v in checks.values())}
        if status == "pass" and not rec["verified"]:
            raise ValueError("cannot report pass with a failing check")
        _atomic_write(d / f"{message_id}.{role}.json", json.dumps(rec))
        return rec

    def reports(self) -> list[dict]:
        d = self.root / "reports"
        return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []

    # --- stage state ---
    STAGES = ("open", "in-progress", "needs-human", "blocked-access", "review", "finished")

    def stage(self, new: str | None = None) -> str:
        path = self.root / "stage"
        if new is None:
            return path.read_text().strip() if path.exists() else "open"
        if new not in self.STAGES:
            raise ValueError(f"unknown stage {new!r}")
        _atomic_write(path, new)
        return new
