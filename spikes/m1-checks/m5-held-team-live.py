"""Live check of LocalSbxSandboxProvider.start_team / restart_team / close against a real sandbox.

Run on the Mac via m5-held-team-live.sh <sandbox>. Needs an existing, running sandbox (e.g. bayto-dev
created by dev-team/run.sh) with NO team running. Uses the real `sbx` CLI; touches no database.

Checks: (1) start_team returns only after team-started exists and the seats stay alive after it returns;
(2) a second start_team is a no-op; (3) close() leaves the team running; (4) a fresh provider adopts it
without starting a second set of seats; (5) restart_team replaces it (same seat count); (6) release_team
ends the holder and the team dies (the open question: does ending the sandbox-side holder stop the team?).
"""
import asyncio
import datetime
import subprocess
import sys
import time
import uuid
from pathlib import Path

from orchestrator.sandbox.local import LocalSbxSandboxProvider
from orchestrator.sandbox.provider import SandboxInfo

COUNT_SEATS = ('n=0; for d in /proc/[0-9]*; do tr "\\0" "\\n" < $d/environ 2>/dev/null '
               '| grep -q "^FACTORY_ROLE=" && n=$((n+1)); done; echo $n')
results: list[tuple[str, bool, str]] = []


def seats(name: str) -> int:
    out = subprocess.run(["sbx", "exec", name, "bash", "-lc", COUNT_SEATS],
                         capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
    return int(out[-1]) if out and out[-1].isdigit() else -1


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(("PASS " if ok else "FAIL ") + label + (f": {detail}" if detail else ""), flush=True)


async def main(name: str) -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    info = SandboxInfo(id=uuid.uuid4(), task_id=uuid.uuid4(), provider="local-sbx", name=name,
                       status="running", image=None, created_at=now, closed_at=None)
    provider = LocalSbxSandboxProvider(sessionmaker=None)
    try:
        check("precondition: no seats running before start", seats(name) == 0, f"seats={seats(name)}")

        t0 = time.monotonic()
        await provider.start_team(info)
        took = time.monotonic() - t0
        n = seats(name)
        check("start_team returned with the team up", n > 0, f"{took:.0f} s, seats={n}")
        time.sleep(20)  # the old one-shot exec lost the team the moment it returned
        n2 = seats(name)
        check("seats still alive 20 s after start_team returned", n2 == n and n2 > 0, f"seats={n2}")

        t1 = time.monotonic()
        await provider.start_team(info)
        check("second start_team is a no-op", time.monotonic() - t1 < 5, f"{time.monotonic() - t1:.1f} s")

        await provider.close()  # drops the host-side client only
        time.sleep(15)
        n3 = seats(name)
        check("close() leaves the team running (a sandbox stops only on user action)", n3 == n, f"seats={n3}")

        fresh = LocalSbxSandboxProvider(sessionmaker=None)  # as after an orchestrator restart
        t2 = time.monotonic()
        await fresh.start_team(info)
        n4 = seats(name)
        check("a fresh provider adopts the running team without starting a second set",
              n4 == n and time.monotonic() - t2 < 20, f"{time.monotonic() - t2:.1f} s, seats={n4}")

        t3 = time.monotonic()
        await fresh.restart_team(info)
        n5 = seats(name)
        check("restart_team replaces the team (same seat count, not doubled)", n5 == n,
              f"{time.monotonic() - t3:.0f} s, seats={n5}")

        await fresh.release_team(info)
        time.sleep(15)
        n6 = seats(name)
        check("release_team ends the holder and the team dies", n6 == 0, f"seats={n6}")
    except Exception as exc:  # report, still clean up
        check("unexpected error", False, repr(exc))
    finally:
        await provider.release_team(info)  # leave nothing running
    fails = sum(1 for _, ok, _ in results if not ok)
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    report = out_dir / f"m5-held-team-{now:%Y%m%d-%H%M%S}.md"
    report.write_text(f"# m5-held-team live check on {name}\n\n" + "\n".join(
        f"- {'PASS' if ok else 'FAIL'}: {label}" + (f" ({d})" if d else "") for label, ok, d in results)
        + f"\n\nSummary: {len(results) - fails} pass, {fails} fail.\n")
    print(f"\nSummary: {len(results) - fails} pass, {fails} fail. Report: {report}")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: m5-held-team-live.py <sandbox-name>")
    sys.exit(asyncio.run(main(sys.argv[1])))
