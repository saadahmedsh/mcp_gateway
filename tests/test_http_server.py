"""Tests for the authenticated Streamable HTTP gateway entrypoint."""

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from gateway.config import Settings
from gateway.http_server import create_http_app


def _settings(tmp_path: Path, token: str | None = None) -> Settings:
    """Build isolated HTTP settings for one test application."""

    return Settings(
        environment="test",
        state_store_backend="memory",
        database_path=tmp_path / "gateway.sqlite",
        audit_log_path=tmp_path / "audit.jsonl",
        http_auth_mode="static_token" if token is not None else "none",
        http_auth_token=SecretStr(token) if token is not None else None,
    )


@pytest.mark.asyncio
async def test_health_endpoints_report_status(tmp_path: Path) -> None:
    """Verify liveness, startup, and dependency readiness endpoints."""

    app = create_http_app(_settings(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/livez")).json()["status"] == "ok"
        assert (await client.get("/startupz")).json()["status"] == "ok"
        readiness = await client.get("/readyz")
        assert readiness.status_code == 200
        assert readiness.json()["checks"] == {"state_store": True, "policy": True}


@pytest.mark.asyncio
async def test_http_requests_require_configured_bearer_token(tmp_path: Path) -> None:
    """Reject unauthenticated HTTP requests and accept the configured token."""

    app = create_http_app(_settings(tmp_path, token="local-test-token"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/livez")
        assert health.status_code == 200
        denied = await client.get("/mcp")
        assert denied.status_code == 401
        allowed = await client.get(
            "/not-a-route",
            headers={"Authorization": "Bearer local-test-token"},
        )
        assert allowed.status_code == 404
