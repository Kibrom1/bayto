"""The environment handed to programs Bayto starts (`claude`, `sbx`, dev-team/run.sh).

The orchestrator is pointed at an OpenTelemetry collector with OTEL_EXPORTER_OTLP_ENDPOINT (gRPC, port 4317). Programs it
starts inherit that variable; `claude` then tries to export its own traces and metrics there over HTTP, which a gRPC
port answers with garbage ("BadStatusLine"), so the log fills with retry errors and each run is slowed down. The
orchestrator's own telemetry is configured in-process, so children get no OTEL_* variables at all.

The API access token (BAYTO_API_TOKEN, see auth.py) is stripped for the same reason.
The local-model gateway settings (BAYTO_GATEWAY_URL, BAYTO_GATEWAY_TOKEN) are the orchestrator's alone and are stripped
the same way: `sbx`, dev-team/run.sh and the helper `claude` calls must not see the token. It reaches exactly one place,
the `claude` process of a seat on a gateway model, through the explicit `extra` of that seat's turn (see
`sandbox/local.py`); `extra` is applied after the stripping, so it is never removed.
"""
import os

_ORCHESTRATOR_ONLY = ("BAYTO_GATEWAY_URL", "BAYTO_GATEWAY_TOKEN", "BAYTO_API_TOKEN")

# Provider keys for discussion seats on OpenAI or Gemini (see providers/registry.py). Only the orchestrator's own HTTP
# calls use them, read straight from its environment. No program it starts needs them, so none of them gets them:
# `claude` and `sbx` would otherwise hand the key to whatever a seat runs, and git and gh have no use for it.
# OLLAMA_HOST and OPENAI_BASE_URL are addresses, not secrets, and stay.
_PROVIDER_SECRETS = ("OPENAI_API_KEY", "GEMINI_API_KEY")


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_") and k not in _ORCHESTRATOR_ONLY and k not in _PROVIDER_SECRETS}
    env.update(extra or {})
    return env
