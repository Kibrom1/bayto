"""Read the audit events the app emitted (as `audit.<action>` spans) out of the shared in-memory span exporter."""
import json
from types import SimpleNamespace


def audit_events(span_exporter, **where):
    out = []
    for span in span_exporter.get_finished_spans():
        a = span.attributes or {}
        if not a.get("bayto.audit"):
            continue
        ev = SimpleNamespace(action=a["audit.action"], actor=a["audit.actor"], summary=a["audit.summary"],
                             task_id=a.get("audit.task_id"), session_id=a.get("audit.session_id"),
                             subject_type=a.get("audit.subject_type"), subject_id=a.get("audit.subject_id"),
                             detail=json.loads(a["audit.detail"]))
        if all(getattr(ev, k) == (str(v) if v is not None else None) for k, v in where.items()):
            out.append(ev)
    return out
