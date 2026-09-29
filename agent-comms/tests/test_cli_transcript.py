"""M2.5: the `acp transcript` CLI subcommand is viewer-scoped by `--role` (the breaking
Conversation.transcript(viewer) signature change) -- not the old always-'all'-only view
that silently leaked recipients/moderator messages to every caller. The CLI's own `send`
subcommand has no `--visibility` flag (out of scope here), so the recipients-visibility
fixture message is written directly through the transport, the same wire shape a
`recipients`-visibility sender would produce."""
from acp.cli import main
from acp.models import Envelope
from acp.transport import FileTransport


def test_transcript_hides_a_recipients_message_from_a_non_recipient(tmp_path, capsys):
    FileTransport(tmp_path).send(Envelope(
        conversation_id="c", from_="developer", to=["qa"], kind="note", body="secret",
        visibility="recipients",
    ))
    base = ["--dir", str(tmp_path), "--conv", "c", "--roles", "coordinator,developer,qa"]

    assert main(base + ["--role", "coordinator", "transcript"]) == 0
    assert capsys.readouterr().out == ""  # coordinator is neither sender nor recipient

    assert main(base + ["--role", "qa", "transcript"]) == 0
    assert "secret" in capsys.readouterr().out

    assert main(base + ["--role", "developer", "transcript"]) == 0
    assert "secret" in capsys.readouterr().out  # sender always sees its own send


def test_transcript_shows_an_all_visibility_message_to_everyone(tmp_path, capsys):
    base = ["--dir", str(tmp_path), "--conv", "c", "--roles", "coordinator,developer"]

    assert main(base + ["--role", "coordinator", "send", "--to", "developer", "--kind", "assignment", "go"]) == 0
    capsys.readouterr()

    for role in ("coordinator", "developer"):
        assert main(base + ["--role", role, "transcript"]) == 0
        assert "go" in capsys.readouterr().out
