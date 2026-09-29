"""Roster + role catalog -> team.tsv, per-role briefs, tool policy and network allowlist (M1.8/M1.9)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MODEL = "claude-haiku"


class RosterError(ValueError):
    pass


def load_catalog(root: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(root)
    cat = {}
    for f in sorted(root.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        brief = root / f"{d['id']}.md"
        d["_brief_text"] = brief.read_text() if brief.exists() else f"# {d['name']}\n"
        cat[d["id"]] = d
    return cat


def _seat_ids(entries: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for e in entries:
        n = seen.get(e["role"], 0) + 1
        seen[e["role"]] = n
        out.append(e["role"] if n == 1 else f"{e['role']}-{n}")
    return out


def build_team(entries: list[dict], catalog: dict[str, dict], out_dir: str | Path, topic: str = "",
               default_model: str = DEFAULT_MODEL, conversation: str = "task") -> dict[str, Any]:
    """entries: [{"role": id, "model": optional, "harness": optional, "provider": optional}, ...]"""
    if not entries:
        raise RosterError("roster is empty")
    for e in entries:
        if e["role"] not in catalog:
            raise RosterError(f"unknown role {e['role']!r}; known: {sorted(catalog)}")
    out = Path(out_dir)
    (out / "roles").mkdir(parents=True, exist_ok=True)
    seats = _seat_ids(entries)

    team_rows, tools, network = [], {}, set()
    for seat, e in zip(seats, entries):
        role = catalog[e["role"]]
        model = e.get("model", default_model)
        team_rows.append(f"{seat}\t{e.get('harness', 'claude')}\t{e.get('provider', 'anthropic')}\t{model}")
        tools[seat] = {"allow": role["tools"]["allow"], "deny": role["tools"]["deny"]}
        network.update(role.get("team_needs", {}).get("network", []))
    (out / "team.tsv").write_text("\n".join(team_rows) + "\n")

    table = "\n".join(f"- `{s}` — {catalog[e['role']]['name']}: {catalog[e['role']]['responsibility']}"
                      for s, e in zip(seats, entries))
    for seat, e in zip(seats, entries):
        role = catalog[e["role"]]
        text = (f"{role['_brief_text'].rstrip()}\n\n## Your seat\nYou are `{seat}`. Task: {topic or '(sent by the human)'}\n\n"
                f"## The team\n{table}\n- `human` — the person who owns the task; the only one who sends `decision`.\n\n"
                f"## How to talk\nRead with `acp read`, reply with `acp send --to ROLE --kind KIND \"text\"` "
                f"(you may send: {', '.join(role['may_send'])}). After sending, end your turn; never poll.\n"
                f"Tools you may use: {', '.join(role['tools']['allow'])}. Denied: {', '.join(role['tools']['deny']) or 'none'}.\n")
        (out / "roles" / f"{seat}.md").write_text(text)

    (out / "tools.json").write_text(json.dumps(tools, indent=2))
    (out / "network.json").write_text(json.dumps(sorted(network), indent=2))
    roster = {"conversation": conversation, "roles": seats}
    (out / "roster.json").write_text(json.dumps(roster, indent=2))
    return {"seats": seats, "network": sorted(network), "tools": tools, "roster": roster}
