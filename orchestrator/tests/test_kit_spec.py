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


def test_kit_does_not_redefine_the_agents_anthropic_credential():
    """`sbx env create` rejects a kit that defines a credential the `claude` agent already provides
    ('credential for service "anthropic" defined in both "claude" and "bayto-team"')."""
    import yaml
    from pathlib import Path

    spec = yaml.safe_load((Path(__file__).resolve().parents[2] / "kits" / "bayto-team" / "spec.yaml").read_text())
    services = [c.get("service") for c in spec.get("credentials", [])]
    assert "anthropic" not in services
