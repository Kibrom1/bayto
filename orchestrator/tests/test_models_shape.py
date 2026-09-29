"""No live DB needed: checks the ORM metadata shape itself, and that the mirror tables
really do mirror acp's wire fields (not invented ones)."""
from acp.models import Envelope

from orchestrator.models import Base, Message, Report


def test_all_expected_tables_present():
    assert set(Base.metadata.tables) == {
        "task", "agent", "mode", "session", "session_agent", "sandbox", "turn", "artifact",
        "message", "report",
    }


def test_message_mirrors_every_envelope_field():
    envelope_fields = set(Envelope.model_fields) - {"from_"} | {"from"}  # alias
    message_columns = {c.name for c in Message.__table__.columns} - {"session_id"}  # mirror-only addition
    assert envelope_fields == message_columns


def test_report_mirrors_the_report_dict_shape():
    report_dict_fields = {"from", "message_id", "status", "stage", "output_ref", "checks", "summary", "verified"}
    report_columns = {c.name for c in Report.__table__.columns} - {"session_id"}  # mirror-only addition
    assert report_dict_fields == report_columns


def test_message_visibility_check_constraint_matches_envelope_enum():
    check_sql = next(c.sqltext.text for c in Message.__table__.constraints
                      if c.__class__.__name__ == "CheckConstraint")
    for value in ("all", "recipients", "moderator"):
        assert value in check_sql
