"""Proves acp (agent-comms) is wired in as a real path dependency, not copy-pasted."""
import acp


def test_acp_is_the_agent_comms_package():
    assert acp.__file__.endswith("agent-comms/src/acp/__init__.py") or "agent-comms" in acp.__file__
    assert {"Envelope", "Kind", "Roster", "FileTransport", "Transport", "Conversation"} <= set(acp.__all__)
