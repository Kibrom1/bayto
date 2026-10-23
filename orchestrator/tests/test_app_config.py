"""GET /app-config: BAYTO_FEEDBACK_URL is exposed only when it is a plain https or mailto URL."""
import logging

import pytest
from fastapi.testclient import TestClient

from orchestrator.app import app


def _get(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("BAYTO_FEEDBACK_URL", raising=False)
    else:
        monkeypatch.setenv("BAYTO_FEEDBACK_URL", value)
    with TestClient(app) as client:
        r = client.get("/app-config")
    assert r.status_code == 200
    body = r.json()
    assert (body.pop("auth_required"), body.pop("authenticated")) == (False, True)
    return body


def test_unset_means_no_feedback_url(monkeypatch):
    assert _get(monkeypatch, None) == {"feedback_url": None}
    assert _get(monkeypatch, "  ") == {"feedback_url": None}


@pytest.mark.parametrize("url", ["https://forms.example.com/bayto", "https://example.com/f?src=app", "mailto:pilot@example.com"])
def test_valid_https_and_mailto_are_exposed(monkeypatch, url):
    assert _get(monkeypatch, f" {url} ") == {"feedback_url": url}


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,<script>1</script>", "http://example.com/f", "ftp://example.com",
    "//example.com/f", "/feedback", "example.com", "https://", "mailto:", "https://exa mple.com", "https://a.com/\nx",
])
def test_other_schemes_are_ignored_and_logged(monkeypatch, caplog, url):
    with caplog.at_level(logging.WARNING):
        assert _get(monkeypatch, url) == {"feedback_url": None}
    assert any("BAYTO_FEEDBACK_URL ignored" in r.message for r in caplog.records)
