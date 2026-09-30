"""OpenTelemetry wiring (M2.10): a global TracerProvider configured once, at app startup,
via `configure_tracing()`. Every call site gets its own tracer the standard OTel way --
`trace.get_tracer(__name__)` -- rather than through a constructor-injected seam like
SbxRunner/HandRaiseScorer/etc.; that's OTel's own idiom, not this codebase's usual
dependency-injection pattern, and mixing the two would just be friction for no benefit.

Exporter: OTLP if `OTEL_EXPORTER_OTLP_ENDPOINT` is set (a config value, never hardcoded,
same pattern as model ids throughout this project), otherwise a `ConsoleSpanExporter` --
no-collector-required by default. Standing up a real collector/Grafana integration is the
doc's own "-> Grafana" arrow, explicitly a later concern, not built here.

Sampling: always-on (100%) -- chat-paced traffic, no volume reason to sample down.

`configure_tracing()` is called from `app.py`'s `lifespan`, not at module import time: it
must not run during test collection/import (tests install their own InMemorySpanExporter-
backed provider instead, via `tests/conftest.py`'s session-scoped fixture, which runs
before any test needs one). `set_tracer_provider()` only ever succeeds once per process --
whichever caller gets there first wins, and every later call is a harmless no-op (a logged
warning). Calling this from `lifespan` (which only actually runs when the ASGI app starts
serving -- e.g. via `TestClient`, not a bare `ASGITransport`) rather than at import time
keeps that ordering the right way around for both production and tests.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

SERVICE_NAME = "bayto-orchestrator"


def configure_tracing() -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter()  # reads OTEL_EXPORTER_OTLP_ENDPOINT itself
    else:
        exporter = ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
