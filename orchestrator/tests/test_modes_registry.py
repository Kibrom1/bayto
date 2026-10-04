"""M2.8: resolve_mode() -- pure filesystem + parsing, no DB needed. Uses the same
tests/fixtures/modes/ fixture files test_api_sessions.py points BAYTO_MODES_DIR at."""
from pathlib import Path

import pytest

from orchestrator.modes_registry import ModeNotFoundError, ModeParseError, modes_dir, resolve_mode

FIXTURES = Path(__file__).parent / "fixtures" / "modes"


def test_modes_dir_defaults_to_the_repo_root_modes_directory(monkeypatch):
    monkeypatch.delenv("BAYTO_MODES_DIR", raising=False)
    assert modes_dir().name == "modes"
    assert modes_dir().exists()


def test_modes_dir_reads_the_env_var_override(monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    assert modes_dir() == FIXTURES


def test_resolve_mode_parses_a_real_fixture(monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    mode = resolve_mode("round-robin-test")
    assert mode.name == "round-robin-test"
    assert mode.floor_policy == "round-robin"


def test_resolve_mode_raises_not_found_for_a_missing_file(monkeypatch):
    monkeypatch.setenv("BAYTO_MODES_DIR", str(FIXTURES))
    with pytest.raises(ModeNotFoundError):
        resolve_mode("no-such-mode")


def test_resolve_mode_raises_parse_error_for_invalid_yaml(monkeypatch, tmp_path):
    (tmp_path / "broken.yaml").write_text(": not: valid: yaml: [")
    monkeypatch.setenv("BAYTO_MODES_DIR", str(tmp_path))
    with pytest.raises(ModeParseError):
        resolve_mode("broken")


# ---------------------------------------------------------------- M2.11 seeded modes
# The real, checked-in modes/*.yaml files parse cleanly -- no fixture, no DB, no network.

def test_resolve_mode_parses_the_real_open_chat_mode(monkeypatch):
    monkeypatch.delenv("BAYTO_MODES_DIR", raising=False)
    mode = resolve_mode("open-chat")
    assert mode.floor_policy == "raise-hand"
    assert mode.roles == {"*": ["note", "question", "answer", "x-hand-raise"]}
    assert mode.stop_rules["max_rounds"] is None
    assert mode.synthesis_prompt_hint is None


def test_resolve_mode_parses_the_real_debate_mode(monkeypatch):
    monkeypatch.delenv("BAYTO_MODES_DIR", raising=False)
    mode = resolve_mode("debate")
    assert mode.floor_policy == "round-robin"
    assert mode.roles == {"*": ["note", "question", "answer", "x-hand-raise"]}
    assert mode.stop_rules["max_rounds"] == 6
    assert mode.synthesis_prompt_hint and "verdict" in mode.synthesis_prompt_hint


def test_resolve_mode_parses_the_real_brainstorm_mode(monkeypatch):
    monkeypatch.delenv("BAYTO_MODES_DIR", raising=False)
    mode = resolve_mode("brainstorm")
    assert mode.floor_policy == "round-robin"
    assert mode.stop_rules["max_rounds"] == 6
    assert mode.synthesis_prompt_hint and "cluster" in mode.synthesis_prompt_hint.lower()
