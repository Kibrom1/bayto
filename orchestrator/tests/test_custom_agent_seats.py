"""Custom agents (api/agents.py): what the sandbox gives their seat."""
import yaml

from orchestrator.sandbox.provider import SeatSpec
from orchestrator.sandbox.provision import READ_ONLY, write_catalog
from orchestrator.build_policy import build_seat_tools


def test_a_custom_agent_seat_is_read_only_with_writes_and_shell_denied(tmp_path):
    # A custom agent's tools list is empty (its preset adds nothing), and its role is not a Build role.
    tools, deny = build_seat_tools("compliance-officer", (), {"test_command": "pytest"})
    assert tools == () and deny == ()
    seat = SeatSpec(role="compliance-officer", name="Compliance Officer", brief="Check plans.", tools=tools, deny=deny)
    write_catalog([seat], tmp_path / "roles", tmp_path / "roster.txt")
    spec = yaml.safe_load((tmp_path / "roles" / "compliance-officer.yaml").read_text())
    assert spec["tools"]["allow"] == READ_ONLY
    assert {"Write", "Edit", "WebFetch", "Bash"} <= set(spec["tools"]["deny"])
