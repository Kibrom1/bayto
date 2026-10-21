"""Review hardening: secrets in folders, system folders, Host header, unknown recipients, Unicode names."""
import httpx
import pytest

from orchestrator import context_extract as cx
from orchestrator.app import app
from orchestrator.api.agents import role_slug


def test_folder_withholds_files_with_credentials(tmp_path):
    (tmp_path / "notes.md").write_text("hello")
    (tmp_path / "config.yaml").write_text("db_password: hunter2\n")
    (tmp_path / "deploy.sh").write_text("export TOKEN=ghp_abcdef1234567890abcd\n")
    _, text, _ = cx.extract_folder(str(tmp_path))
    assert "hello" in text and "hunter2" not in text and "ghp_abcdef" not in text
    assert "config.yaml" in text and "credentials" in text


def test_system_folder_refused():
    with pytest.raises(cx.ContextError):
        cx.extract_folder("/etc")


def test_role_slug_keeps_non_latin_letters():
    assert len(role_slug("ሕግ አማካሪ")) >= 2 and role_slug("5G") == "5g"


@pytest.mark.asyncio
async def test_foreign_host_refused():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://attacker.example") as c:
        assert (await c.get("/healthz")).status_code == 400
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as c:
        assert (await c.get("/healthz")).status_code == 200
