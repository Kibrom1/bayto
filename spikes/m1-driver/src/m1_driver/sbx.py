"""sbx lifecycle seam (M1.10 spike): the same four verbs M2.4's LocalSbxSandboxProvider
uses (docs/product-design.md's local-sbx table) -- env create, exec start-team, exec
crew-notify <role>, env rm -- minus the DB-row bookkeeping (there is no M2 Sandbox table
pre-M2, and this spike has nothing to persist across restarts). `crew-notify`'s existing
role argument already covers Herdr-session-per-agent routing -- no new concept needed
here, per the architect's scope.

SubprocessSbxLifecycle's argv shapes are UNVERIFIED against a real `sbx` binary -- same
caveat class, same unresolved questions, as M2.4's SubprocessSbxRunner/LocalSbxSandboxProvider
module docstring (no `sbx` CLI in this dev-team sandbox to confirm end to end). See
FINDINGS.md for the compound gap this creates together with the Anthropic-call gap.
"""
from __future__ import annotations

import subprocess
from typing import Protocol


class SbxCommandError(RuntimeError):
    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"command {argv!r} exited {returncode}: {stderr}")


class SbxLifecycle(Protocol):
    def create(self, name: str) -> None: ...
    def start_team(self, name: str) -> None: ...
    def crew_notify(self, name: str, role: str) -> None: ...
    def remove(self, name: str) -> None: ...


class SubprocessSbxLifecycle:
    """Real `sbx` CLI invocation. UNVERIFIED: see module docstring."""

    def __init__(self, *, sbxenv_path: str = "team.sbxenv.yaml") -> None:
        self._sbxenv_path = sbxenv_path

    def _run(self, argv: list[str]) -> None:
        result = subprocess.run(["sbx", *argv], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SbxCommandError(argv, result.returncode, result.stderr)

    def create(self, name: str) -> None:
        self._run(["env", "create", self._sbxenv_path, "--env-arg", f"name={name}", "--auto-approve"])

    def start_team(self, name: str) -> None:
        self._run(["exec", name, "start-team"])

    def crew_notify(self, name: str, role: str) -> None:
        self._run(["exec", name, "crew-notify", role])

    def remove(self, name: str) -> None:
        self._run(["env", "rm", name, "--auto-approve"])


class FakeSbxLifecycle:
    """Records every call -- for loop tests; no subprocess."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def create(self, name: str) -> None:
        self.calls.append(("create", name))

    def start_team(self, name: str) -> None:
        self.calls.append(("start_team", name))

    def crew_notify(self, name: str, role: str) -> None:
        self.calls.append(("crew_notify", name, role))

    def remove(self, name: str) -> None:
        self.calls.append(("remove", name))
