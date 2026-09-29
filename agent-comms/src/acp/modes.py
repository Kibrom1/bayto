"""Mode files -> Roster/SendPolicy/route table (M2.5).

A mode is data, not code (docs/agent-communication-protocol.md, 'Reuse'): `modes/<name>.yaml`
declares which Envelope kinds each role may emit, the floor policy, an optional pipeline
route table, and an optional moderator role for `moderator`-visibility messages. Parsed at
session start straight into a `ModeConfig`; never round-tripped into
`orchestrator.models.Mode.phases_json` (that column is about turn-taking/phases, a
different concern from who-may-address-whom -- see docs/decisions.md, 2026-09-29).

Example (docs/agent-communication-protocol.md's `code-factory` mode):

    mode: code-factory
    floor_policy: pipeline
    roles:
      coordinator: { may_send: [assignment, question, decision-request, summary, note] }
      developer:   { may_send: [report, question, answer, note, x-code.review-request] }
      qa:          { may_send: [report, critique, question, note] }
      human:       { may_send: [decision, question, note] }
    route:
      start: coordinator
      developer.report.implemented: qa
      qa.report.review-fail: developer
      qa.report.review-pass: coordinator
    moderator: coordinator   # optional; unset means moderator-visibility matches nobody
                             # but the sender (safe default, not a crash)
    send:                    # optional; Roster's own {sender: [recipients]} shape, unused
                             # by pipeline mode (see ModeConfig.send_roster)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Roster


@dataclass(frozen=True, slots=True)
class ModeConfig:
    name: str
    floor_policy: str
    roles: dict[str, list[str] | None]  # role -> allowed Envelope kinds (None = unrestricted)
    route: dict[str, str] = field(default_factory=dict)
    moderator: str | None = None
    send: dict[str, list[str]] | None = None  # explicit Roster.send override; None = derive

    def send_roster(self) -> Roster:
        """Pipeline modes route addressing through `route` (e.g. "who's next" is already
        answered by "developer.report.implemented: qa"), so they get a permissive Roster
        and rely on `SendPolicy` (kind) plus the route table for real access control. A
        mode needing direct addressing declares an explicit `send:` block instead."""
        if self.send is not None:
            return Roster(roles=list(self.roles), send=self.send)
        if self.floor_policy == "pipeline":
            return Roster(roles=list(self.roles), send={"*": ["*"]})
        return Roster(roles=list(self.roles))

    def next_role(self, key: str) -> str | None:
        """Pipeline route-table lookup. `key` is "start" for the first hop, or
        "<role>.<kind>.<status>" for report-driven routing (e.g.
        "developer.report.implemented")."""
        return self.route.get(key)


def load_mode(path: str | Path) -> ModeConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    roles = {role: (cfg or {}).get("may_send") for role, cfg in raw.get("roles", {}).items()}
    return ModeConfig(
        name=raw["mode"],
        floor_policy=raw["floor_policy"],
        roles=roles,
        route=raw.get("route", {}),
        moderator=raw.get("moderator"),
        send=raw.get("send"),
    )
