import json

from acp.cli import main


def test_tool_flags_prints_one_flag_per_line(tmp_path, capsys):
    tools_json = tmp_path / "tools.json"
    tools_json.write_text(json.dumps({
        "backend-engineer": {"allow": ["Read", "Write", "Bash"], "deny": ["WebFetch"]},
        "coordinator": {"allow": ["Read"], "deny": ["Write", "Bash"]},
    }))

    rc = main(["tool-flags", "--tools-json", str(tools_json), "--role", "backend-engineer"])

    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["--allowedTools", "Read", "Write", "Bash", "--disallowedTools", "WebFetch", "--permission-mode", "dontAsk"]


def test_tool_flags_unknown_role_prints_nothing(tmp_path, capsys):
    tools_json = tmp_path / "tools.json"
    tools_json.write_text(json.dumps({"coordinator": {"allow": ["Read"], "deny": []}}))

    rc = main(["tool-flags", "--tools-json", str(tools_json), "--role", "ghost"])

    assert rc == 0
    assert capsys.readouterr().out == ""
