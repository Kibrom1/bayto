#!/usr/bin/env python3
import os
import sys
import json
import random
import string
import tempfile
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

# Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "llama3")
FACTORY_DIR = Path(os.getenv("FACTORY_DIR", str(Path.home() / "work" / "factory")))
SEQ_LOCK_STALE_SECS = 60  # a seq lock older than this was left by a crashed command


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def rand6() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _atomic_write_json(target: Path, obj: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _call_ollama(url: str, model: str, prompt: str) -> str:
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response).get("response", "")


def _find_assignment(messages_dir: Path, role: str) -> dict | None:
    last_assignment = None
    last_seq = -1
    if not messages_dir.is_dir():
        return None
    for path in messages_dir.glob("*.json"):
        try:
            with open(path) as f:
                env = json.load(f)
        except Exception:
            continue
        if env.get("kind") == "assignment" and env.get("to") == role:
            seq = env.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
                last_assignment = env
    return last_assignment


def _fs_now(directory: Path) -> float:
    """The current time as the filesystem under `directory` stamps it (a just-made file's mtime)."""
    fd, name = tempfile.mkstemp(dir=directory, prefix=".clock-")
    os.close(fd)
    try:
        return os.stat(name).st_mtime
    finally:
        os.unlink(name)


def _break_stale_lock(lock: Path) -> bool:
    """Remove a stale sequence lock; True if the caller may retry at once.

    Same protocol as handoff_compat._break_stale_lock (this module is copied standalone, so it
    cannot import it): take "<lock>.break" first and re-check the age while holding it, so two
    waiters cannot both break the lock and end up holding it together. Ages are measured against
    the filesystem's own clock, not time.time(), so a skewed clock cannot make a live lock look stale.
    """
    brk = lock.with_name(lock.name + ".break")
    try:
        brk.mkdir()
    except FileExistsError:
        try:
            brk_age = _fs_now(lock.parent) - brk.stat().st_mtime
        except FileNotFoundError:
            return True
        if brk_age > SEQ_LOCK_STALE_SECS:
            try:
                brk.rmdir()
            except FileNotFoundError:
                return True  # someone else cleared it: retry at once
            except OSError:
                return False  # cannot clear it (a file, or not empty): wait and count a try
            return True
        return False
    try:
        try:
            # brk was just made, so its mtime is "now" on the filesystem's clock
            age = brk.stat().st_mtime - lock.stat().st_mtime
        except FileNotFoundError:
            return True
        if age <= SEQ_LOCK_STALE_SECS:
            return False  # not stale (re-taken meanwhile, or this clock is ahead): wait, count
        try:
            lock.rmdir()
        except OSError:
            return False
        print(f"handoff: removed stale sequence lock {lock} (age {int(age)}s)", file=sys.stderr)
        return True
    finally:
        try:
            brk.rmdir()
        except OSError:
            pass


def _next_seq(factory_dir: Path) -> int | None:
    # Serialised by a claim directory, same lock pattern as
    # handoff_compat.Factory.next_seq() uses for $FACTORY_DIR/state.json.
    state_file = factory_dir / "state.json"
    if not state_file.exists():
        return None
    claims = factory_dir / "claims"
    lock = claims / ".seq.lock"
    claims.mkdir(parents=True, exist_ok=True)
    tries = 0
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            # same stale-lock recovery as handoff_compat (this module is copied standalone)
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > SEQ_LOCK_STALE_SECS and _break_stale_lock(lock):
                continue
            tries += 1
            if tries > 100:
                raise RuntimeError(f"could not take the sequence lock at {lock} (or {lock}.break); "
                                   "if no handoff command is running, remove it")
            time.sleep(0.05)
    try:
        state = json.loads(state_file.read_text())
        seq = int(state.get("seq", 0)) + 1
        state["seq"] = seq
        state["updated_at"] = now()
        _atomic_write_json(state_file, state)
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            print(f"handoff: sequence lock was removed while held "
                  f"(held longer than {SEQ_LOCK_STALE_SECS}s?)", file=sys.stderr)
    return seq


def main():
    if len(sys.argv) < 2:
        print("Usage: crew-notify-fallback <role>")
        sys.exit(1)

    role = sys.argv[1]
    messages_dir = FACTORY_DIR / "messages"

    last_assignment = _find_assignment(messages_dir, role)
    if not last_assignment:
        print(f"No assignment found for role {role}")
        sys.exit(0)

    prompt = last_assignment.get("body", "Please respond to the current assignment.")
    reply_to = last_assignment.get("from", role)

    try:
        text = _call_ollama(OLLAMA_URL, FALLBACK_MODEL, prompt)
    except Exception as e:
        print(f"Ollama error: {e}")
        sys.exit(1)

    seq = _next_seq(FACTORY_DIR)
    if seq is None:
        print(f"handoff not initialised at {FACTORY_DIR}")
        sys.exit(0)

    state = json.loads((FACTORY_DIR / "state.json").read_text())
    envelope = {
        "message_id": f"fallback-{stamp()}-{rand6()}",
        "run_id": state.get("run_id", ""),
        "task_id": state.get("task_id", ""),
        "attempt": int(state.get("attempt", 1)),
        "seq": seq,
        "from": role,
        "to": reply_to,
        "kind": "note",
        "created_at": now(),
        "requires_ack": False,
        "refs": {},
        "body": text,
        "meta": {
            "fallback": True,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0,
        },
    }

    out_path = messages_dir / f"{stamp()}-{seq:04d}-fallback-{role}-{rand6()}.json"
    _atomic_write_json(out_path, envelope)

    print(f"Fallback response written to {out_path}")


if __name__ == "__main__":
    main()
