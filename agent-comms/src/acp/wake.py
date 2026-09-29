"""Wake-up SPI: tell a role it has mail, without typing into a busy agent."""
from __future__ import annotations

from typing import Protocol

from .transport import FileTransport

OK, BUSY, BLOCKED, UNKNOWN, CLAIMED_ELSEWHERE = 0, 3, 4, 5, 9  # workshop crew-notify exit codes


class Waker(Protocol):
    def status(self, role: str) -> str: ...  # idle | working | blocked | unknown
    def nudge(self, role: str) -> None: ...


def notify(transport: FileTransport, waker: Waker, role: str, message_id: str) -> int:
    """Wake role once per message. Returns an exit code (0 woken, 3 busy, 4 blocked, 9 already claimed)."""
    st = waker.status(role)
    if st == "working":
        return BUSY  # it will see the message on its next read
    if st == "blocked":
        return BLOCKED
    if not transport.claim(f"wake-{role}-{message_id}"):
        return CLAIMED_ELSEWHERE
    waker.nudge(role)
    return OK
