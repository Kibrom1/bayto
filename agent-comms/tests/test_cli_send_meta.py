"""M2.7: `acp send --tokens-in/--tokens-out/--cost` populates envelope.meta, the
required-but-soft-fail budget-accounting convention (docs/agent-communication-protocol.md,
2026-09-29 product-owner decision). Omitting the flags must be byte-for-byte unchanged
from before (empty meta), since most sends (reports, notes) don't carry them."""
from acp.cli import main
from acp.transport import FileTransport


def test_send_with_all_three_flags_populates_meta(tmp_path):
    base = ["--dir", str(tmp_path), "--conv", "c", "--roles", "a,b"]
    rc = main(base + ["--role", "a", "send", "--to", "b", "--kind", "note", "hi",
                       "--tokens-in", "120", "--tokens-out", "45", "--cost", "0.0031"])
    assert rc == 0

    [env] = FileTransport(tmp_path).all()
    assert env.meta == {"tokens_in": 120, "tokens_out": 45, "cost": 0.0031}


def test_send_without_the_flags_leaves_meta_empty(tmp_path):
    base = ["--dir", str(tmp_path), "--conv", "c", "--roles", "a,b"]
    rc = main(base + ["--role", "a", "send", "--to", "b", "--kind", "note", "hi"])
    assert rc == 0

    [env] = FileTransport(tmp_path).all()
    assert env.meta == {}


def test_send_with_only_some_flags_omits_the_rest(tmp_path):
    base = ["--dir", str(tmp_path), "--conv", "c", "--roles", "a,b"]
    rc = main(base + ["--role", "a", "send", "--to", "b", "--kind", "note", "hi", "--tokens-out", "10"])
    assert rc == 0

    [env] = FileTransport(tmp_path).all()
    assert env.meta == {"tokens_out": 10}
