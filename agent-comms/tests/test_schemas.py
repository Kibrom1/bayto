"""M1.1 / M1.1b: validate schema/{message,report,hand-raise}.v1.json against
both hand-written samples and the real output of the current implementation.

message.v1.json and report.v1.json describe the *resolved* acp/1 shapes (see
docs/decisions.md, 2026-09-29 "M1.1 schema/implementation resolutions"):
doc and code were reconciled, so there is no more "doc says X, code does Y"
framing to test for those two. hand-raise.v1.json is still a documented gap
(no producing model exists) and is tested accordingly.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from acp.core import Conversation
from acp.models import Envelope, Roster
from acp.transport import FileTransport

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text())


MESSAGE_SCHEMA = load_schema("message.v1.json")
REPORT_SCHEMA = load_schema("report.v1.json")
HAND_RAISE_SCHEMA = load_schema("hand-raise.v1.json")


@pytest.mark.parametrize("schema", [MESSAGE_SCHEMA, REPORT_SCHEMA, HAND_RAISE_SCHEMA])
def test_schema_files_are_valid_json_schema(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


# ---------------------------------------------------------------- message.v1.json

def test_sample_message_validates():
    sample = {
        "protocol": "acp/1", "message_id": "m1", "conversation_id": "conv42",
        "attempt": 1, "seq": 17, "from": "skeptic", "to": ["advocate"],
        "visibility": "all", "kind": "objection", "in_reply_to": "m0",
        "thread_id": "m0", "requires_ack": True,
        "refs": {"output_ref": "git:9f2c1e0", "topic_version": "3"},
        "body": "no", "body_format": "markdown",
        "meta": {"urgency": 0.8}, "created_at": "2026-09-28T14:02:11+00:00",
    }
    jsonschema.validate(sample, MESSAGE_SCHEMA)


def test_doc_example_envelope_now_validates():
    """The doc's own illustrative envelope (docs/agent-communication-protocol.md,
    'Core model and envelope'), after the M1.1b protocol/doc fix (protocol is
    acp/1, refs is an object, visibility uses the all/recipients/moderator
    vocabulary), validates against message.v1.json."""
    doc_example = {
        "protocol": "acp/1", "message_id": "msg-conv42-0017-k3f9qa",
        "conversation_id": "conv42", "attempt": 1, "seq": 17, "from": "skeptic",
        "to": ["advocate"], "visibility": "all", "kind": "objection",
        "in_reply_to": "msg-conv42-0015-a81kd0", "thread_id": "msg-conv42-0012-p0q2zz",
        "requires_ack": True, "refs": {"output_ref": "git:9f2c1e0", "topic_version": "3"},
        "body": "The cost model assumes 40% sandbox idle time; the benchmark showed 12%.",
        "body_format": "markdown", "meta": {"urgency": 0.8, "tokens_out": 142},
        "created_at": "2026-09-28T14:02:11+00:00",
    }
    jsonschema.validate(doc_example, MESSAGE_SCHEMA)


def test_sample_message_rejects_unknown_kind():
    sample = {
        "protocol": "acp/1", "message_id": "m1", "conversation_id": "c",
        "attempt": 1, "seq": 1, "from": "a", "to": ["b"], "visibility": "all",
        "kind": "gossip", "in_reply_to": None, "thread_id": None,
        "requires_ack": False, "refs": {}, "body": "", "body_format": "markdown",
        "meta": {}, "created_at": "2026-01-01T00:00:00+00:00",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, MESSAGE_SCHEMA)


def test_sample_message_rejects_empty_to():
    sample = {
        "protocol": "acp/1", "message_id": "m1", "conversation_id": "c",
        "attempt": 1, "seq": 1, "from": "a", "to": [], "visibility": "all",
        "kind": "note", "in_reply_to": None, "thread_id": None,
        "requires_ack": False, "refs": {}, "body": "", "body_format": "markdown",
        "meta": {}, "created_at": "2026-01-01T00:00:00+00:00",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, MESSAGE_SCHEMA)


def test_sample_message_rejects_unknown_visibility():
    sample = {
        "protocol": "acp/1", "message_id": "m1", "conversation_id": "c",
        "attempt": 1, "seq": 1, "from": "a", "to": ["b"], "visibility": "room",
        "kind": "note", "in_reply_to": None, "thread_id": None,
        "requires_ack": False, "refs": {}, "body": "", "body_format": "markdown",
        "meta": {}, "created_at": "2026-01-01T00:00:00+00:00",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, MESSAGE_SCHEMA)


def test_envelope_dump_validates_against_schema():
    """Requirement (b): the real Pydantic model's JSON output must validate."""
    env = Envelope(conversation_id="c1", from_="moderator", to=["advocate", "skeptic"],
                    kind="assignment", body="go", requires_ack=True)
    jsonschema.validate(env.dump(), MESSAGE_SCHEMA)


def test_envelope_accepts_bare_string_to_and_dump_validates():
    env = Envelope(conversation_id="c1", from_="moderator", to="developer", kind="note")
    assert env.to == ["developer"]  # always stored/serialized as a list
    jsonschema.validate(env.dump(), MESSAGE_SCHEMA)


def test_envelope_refs_object_map_and_dump_validates():
    env = Envelope(conversation_id="c1", from_="a", to=["b"], kind="note",
                    refs={"base_commit": "abc123", "output_commit": "def456"})
    jsonschema.validate(env.dump(), MESSAGE_SCHEMA)


@pytest.mark.parametrize("visibility", ["all", "recipients", "moderator"])
def test_envelope_visibility_values_dump_validate(visibility):
    env = Envelope(conversation_id="c1", from_="a", to=["b"], kind="note", visibility=visibility)
    jsonschema.validate(env.dump(), MESSAGE_SCHEMA)


def test_envelope_rejects_unknown_visibility():
    with pytest.raises(Exception):
        Envelope(conversation_id="c1", from_="a", to=["b"], kind="note", visibility="room")


def test_envelope_dump_validates_for_every_kind_and_extension():
    kinds = ["assignment", "question", "answer", "proposal", "critique", "objection",
              "agree", "hand-raise", "decision-request", "decision", "summary", "note",
              "resume", "report", "x-code.review-request"]
    for kind in kinds:
        env = Envelope(conversation_id="c1", from_="a", to=["*"], kind=kind)
        jsonschema.validate(env.dump(), MESSAGE_SCHEMA)


def test_envelope_dump_after_transport_roundtrip_validates(tmp_path):
    """Exercise the actual send path (assigns seq), not just direct construction."""
    conv = Conversation("c1", Roster(roles=["a", "b"]), FileTransport(tmp_path))
    sent = conv.send("a", ["b"], "note", "hi")
    jsonschema.validate(sent.dump(), MESSAGE_SCHEMA)
    [delivered] = conv.read("b")
    jsonschema.validate(delivered.dump(), MESSAGE_SCHEMA)


# ---------------------------------------------------------------- report.v1.json

def test_sample_report_validates():
    sample = {"from": "b", "message_id": "m1", "status": "review-pass", "stage": "review",
              "output_ref": "git:9f2c1e0", "checks": {"pytest": 0}, "summary": "ok", "verified": True}
    jsonschema.validate(sample, REPORT_SCHEMA)


def test_doc_style_report_now_validates():
    """The doc's Report record, after the M1.1b resolution (checks as an
    object map, message_id/stage/output_ref present), validates."""
    doc_style = {
        "from": "developer", "message_id": "m1", "status": "review-pass", "stage": "review",
        "output_ref": "git:9f2c1e0", "checks": {"pytest": 0}, "summary": "all green",
        "verified": True,
    }
    jsonschema.validate(doc_style, REPORT_SCHEMA)


def test_real_report_output_validates_against_schema(tmp_path):
    """Requirement (b) for report: validate the actual dict the core library
    returns and persists, including stage/output_ref, not a hand-made stand-in."""
    conv = Conversation("c1", Roster(roles=["a", "b"]), FileTransport(tmp_path))
    m = conv.send("a", ["b"], "assignment", "do it")
    rec = conv.report("b", m.message_id, "review-pass", {"pytest": 0, "lint": 0}, "done",
                       stage="review", output_ref="git:deadbeef")
    jsonschema.validate(rec, REPORT_SCHEMA)
    assert conv.transport.reports()[0] == rec
    jsonschema.validate(conv.transport.reports()[0], REPORT_SCHEMA)


def test_real_report_output_validates_with_nulls_and_no_checks(tmp_path):
    conv = Conversation("c1", Roster(roles=["a", "b"]), FileTransport(tmp_path))
    rec = conv.report("b", "m1", "needs-human", {}, "waiting on a decision")
    assert rec["stage"] is None and rec["output_ref"] is None
    assert rec["verified"] is True  # documented quirk: empty checks -> vacuously verified
    jsonschema.validate(rec, REPORT_SCHEMA)


def test_status_is_an_open_string_not_a_closed_enum(tmp_path):
    conv = Conversation("c1", Roster(roles=["a", "b"]), FileTransport(tmp_path))
    for status in ("implemented", "review-fail", "blocked-access", "acknowledged", "x-custom-status"):
        rec = conv.report("b", f"m-{status}", status, {"t": 0}, "")
        jsonschema.validate(rec, REPORT_SCHEMA)


# ---------------------------------------------------------------- hand-raise.v1.json
# Still a documented gap: no code in agent-comms/src/acp constructs or validates this
# shape. Not part of the M1.1b resolution (out of scope, flagged for M2.6).

def test_sample_hand_raise_validates():
    sample = {"participant": "advocate", "reason": "disagree", "urgency": 0.6,
              "in_reply_to": "m1"}
    jsonschema.validate(sample, HAND_RAISE_SCHEMA)


def test_sample_hand_raise_rejects_unknown_reason():
    sample = {"participant": "advocate", "reason": "bored", "urgency": 0.6}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, HAND_RAISE_SCHEMA)


def test_sample_hand_raise_rejects_out_of_range_urgency():
    sample = {"participant": "advocate", "reason": "new_point", "urgency": 1.5}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, HAND_RAISE_SCHEMA)


def test_no_hand_raise_model_exists_yet():
    """Guards the schema's own claim: if this ever starts failing, a HandRaise
    model has been added and hand-raise.v1.json should be re-checked against
    its real output, and the docs/decisions.md gap entry should be closed."""
    import acp
    assert not hasattr(acp, "HandRaise")
    import acp.models as models
    assert not hasattr(models, "HandRaise")
    assert not hasattr(models, "Reason")
