#!/usr/bin/env python3
"""HOST: turn dev-team/roster.txt + the roles/ catalog into team.tsv, per-seat briefs, tools.json and roster.json.
Usage: build-roster.py OUT_DIR [ROSTER_FILE]   (needs PyYAML)"""
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "agent-comms" / "src"))
from acp.roster import build_team, load_catalog  # noqa: E402

out = Path(sys.argv[1])
roster_file = Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "dev-team" / "roster.txt"
entries = []
for line in roster_file.read_text().splitlines():
    parts = line.split("#", 1)[0].split()
    if parts:
        entries.append({"role": parts[0], **({"model": parts[1]} if len(parts) > 1 else {})})
res = build_team(entries, load_catalog(repo / "roles"), out, default_model="claude-sonnet-5", comms="crew",
                 extra=(repo / "dev-team" / "team-rules.md").read_text())
print("Team:", ", ".join(res["seats"]), file=sys.stderr)
