"""The sbx kit schema is strict: `setup.startup[].command` must be an argv list, while
`setup.install[].command` may be a plain script string. A string in `startup` makes
`sbx env create` fail with "cannot unmarshal !!str into []string" (seen live 2026-10-09)."""
from pathlib import Path

import yaml

SPEC = Path(__file__).resolve().parents[2] / "kits" / "bayto-team" / "spec.yaml"


def test_startup_commands_are_argv_lists():
    steps = yaml.safe_load(SPEC.read_text())["setup"]["startup"]
    assert steps
    for step in steps:
        assert isinstance(step["command"], list), step["description"]
        assert all(isinstance(a, str) for a in step["command"])
