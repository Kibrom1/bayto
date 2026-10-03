from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Roster


@dataclass(frozen=True, slots=True)
class ModeConfig:
    name: str
    floor_policy: str
    # role -> allowed Envelope kinds (None = unrestricted). A "*" key (M2.11) stands for
    # "any role" -- for an open-roster mode (e.g. `debate`/`brainstorm`, seeded from an ad
    # hoc set of agent-template instances rather than a fixed team) that can't enumerate
    # every possible participant name up front. Same precedent as `Roster.send`'s own "*"
    # = anyone; `Roster.can_send`/`SendPolicy.check` both fall back to it (see models.py).
    roles: dict[str, list[str] | None]
    route: dict[str, str] = field(default_factory=dict)
    moderator: str | None = None
    send: dict[str, list[str]] | None = None  # explicit Roster.send override; None = derive
    # Raw dict, deliberately not a typed orchestrator config here: agent-comms doesn't
    # depend on orchestrator, so it can't import orchestrator.floor's StopRulesConfig.
    # orchestrator code builds its own typed config from this dict (M2.7's
    # StopRulesConfig.from_mode) -- same "generic here, typed downstream" split as `send`.
    stop_rules: dict[str, object] | None = None
    # M2.11: orchestrator-specific free text (agent-comms itself never reads this), passed
    # through to whichever Synthesizer implementation is in use so a mode can steer the
    # final-synthesis prompt (e.g. debate's "declare a verdict" vs. brainstorm's "cluster and
    # rank") without a new per-mode synthesizer class.
    synthesis_prompt_hint: str | None = None
    # M2.12: optional phase metadata for modes that structure discussion into stages (e.g.
    # brainstorm's diverge -> cluster -> synthesize flow).
    phases: list[dict[str, Any]] | None = None

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
        stop_rules=raw.get("stop_rules"),
        synthesis_prompt_hint=raw.get("synthesis_prompt_hint"),
        phases=raw.get("phases"),
    )
