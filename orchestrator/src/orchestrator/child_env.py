"""The environment handed to programs Bayto starts (`claude`, `sbx`, dev-team/run.sh).

The orchestrator is pointed at an OpenTelemetry collector with OTEL_EXPORTER_OTLP_ENDPOINT (gRPC, port 4317). Programs it
starts inherit that variable; `claude` then tries to export its own traces and metrics there over HTTP, which a gRPC
port answers with garbage ("BadStatusLine"), so the log fills with retry errors and each run is slowed down. The
orchestrator's own telemetry is configured in-process, so children get no OTEL_* variables at all.
"""
import os


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.update(extra or {})
    return env
