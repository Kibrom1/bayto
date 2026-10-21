"""The audit trail: who did what, and what each agent was allowed to do -- as OpenTelemetry events.

`emit()` records one event as a short span (`audit.<action>`) on the `bayto.audit` tracer. The spans go wherever the
rest of Bayto's telemetry goes (OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set), so dashboards, search and retention
are the observability stack's job and nothing about it lives in Bayto's database. The same event is also written as one
JSON line to the `bayto.audit` logger, so a machine with no collector still has a record in its log output.

What is NOT recorded: attachment contents, agent instructions, message text. An event names things and gives sizes and
fingerprints (sha256); the conversation itself stays in the session transcript, where the person already reads it.

Limits worth knowing: the record is only as durable and tamper-resistant as the backend it is sent to (use one that
does not sample or expire audit data, and restrict who can delete from it); an event is emitted just before the change
is committed, so a change that then fails to commit still leaves its event behind.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from opentelemetry import trace

TRACER_NAME = "bayto.audit"
LOGGER = logging.getLogger("bayto.audit")
_tracer = trace.get_tracer(TRACER_NAME)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_url(url: str | None) -> str | None:
    """A link without its query string or fragment: those are where tokens and signed parameters live."""
    return url.split("#", 1)[0].split("?", 1)[0] if url else url


def emit(*, action: str, summary: str, actor: str = "person", task_id: uuid.UUID | None = None,
         session_id: uuid.UUID | None = None, subject_type: str | None = None, subject_id: Any = None,
         detail: dict | None = None) -> None:
    """Record one event. Never raises: auditing must not take a request down with it."""
    try:
        attrs = {
            "bayto.audit": True, "audit.action": action, "audit.actor": actor, "audit.summary": summary[:500],
            "audit.detail": json.dumps(detail or {}, default=str, sort_keys=True),
        }
        for key, value in (("audit.task_id", task_id), ("audit.session_id", session_id),
                           ("audit.subject_type", subject_type), ("audit.subject_id", subject_id)):
            if value is not None:
                attrs[key] = str(value)
        with _tracer.start_as_current_span(f"audit.{action}", attributes=attrs):
            pass
        LOGGER.info(json.dumps({k.removeprefix("audit."): v for k, v in attrs.items() if k != "bayto.audit"}, sort_keys=True))
    except Exception:  # noqa: BLE001
        LOGGER.exception("could not emit audit event %s", action)
