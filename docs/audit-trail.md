# Audit trail

Bayto records who did what, and what each agent was allowed to do, as OpenTelemetry events. Nothing is stored in
Bayto's database, and no message text, attachment contents or agent instructions are recorded (only names, sizes
and sha256 fingerprints).

Each event is an `audit.<action>` span (for example `audit.agent.created`, `audit.session.started`,
`audit.attachment.added`, `audit.pr.opened`) with the attributes `bayto.audit=true`, `audit.action`, `audit.actor`
(`person` or `system`), `audit.summary`, `audit.task_id`, `audit.session_id`, `audit.subject_type`,
`audit.subject_id` and `audit.detail` (JSON). The same event is also written as one line to `logs/api.log`.

## See the events

1. **The log (always on).** `grep AUDIT logs/api.log` (or `tail -f logs/api.log | grep AUDIT`).
2. **A trace viewer (local).** Start Jaeger, then start Bayto with the endpoint set:
   ```
   docker run --rm -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one
   export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
   scripts/bayto-up.sh
   ```
   Open http://localhost:16686, pick service `bayto-orchestrator`, and search with the tag `bayto.audit=true`.
3. **Your own stack (Grafana Tempo, Honeycomb, Datadog...).** Point `OTEL_EXPORTER_OTLP_ENDPOINT` at your collector
   and filter on `bayto.audit=true` or on span names starting with `audit.`.
4. **Console.** `BAYTO_TRACE_CONSOLE=1` prints every span, audit ones included, to the log.

## Keep it trustworthy

The record is only as durable as where it is sent. Use a backend that does not sample or expire `audit.*` spans, and
restrict who can delete from it. An event is emitted just before the change is committed, so a change that then
fails to commit still leaves its event behind.
