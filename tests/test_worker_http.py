"""Tests for the standalone execution-worker HTTP surface."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from gateway.config import Settings
from gateway.models import RiskClass
from gateway.workers.http_server import create_worker_app
from gateway.workers.models import WorkerJob
from gateway.workers.remote import RemoteWorkerClient


def _settings(tmp_path: Path) -> Settings:
    """Build an isolated worker configuration for one HTTP test."""

    return Settings(
        environment="test",
        database_path=tmp_path / "gateway.sqlite",
        audit_log_path=tmp_path / "audit.jsonl",
        worker_shared_secret=SecretStr("test-secret"),
    )


@pytest.mark.asyncio
async def test_worker_rejects_invalid_hmac(tmp_path: Path) -> None:
    """Verify forged jobs are rejected before entering the queue."""

    app = create_worker_app(_settings(tmp_path))
    job = WorkerJob(
        job_id="job-1",
        call_id="call-1",
        tenant_id="tenant-1",
        tool_name="db_query",
        risk_class=RiskClass.READ_ONLY,
        arguments={"query": "SELECT 1"},
        idempotency_key="key-1",
        submitted_at=datetime.now(UTC),
        auth_token="forged",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://worker"
    ) as client:
        response = await client.post("/jobs", json=job.model_dump(mode="json"))

    assert response.status_code == 401
    assert response.json()["error_code"] == "tool_execution_failed"


@pytest.mark.asyncio
async def test_worker_liveness_does_not_require_pool(tmp_path: Path) -> None:
    """Verify liveness remains available before readiness starts."""

    app = create_worker_app(_settings(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://worker"
    ) as client:
        live = await client.get("/livez")
        ready = await client.get("/readyz")

    assert live.status_code == 200
    assert ready.status_code == 503


@pytest.mark.asyncio
async def test_remote_client_executes_through_worker_app(tmp_path: Path) -> None:
    """Verify a signed remote request crosses the real worker HTTP route."""

    settings = _settings(tmp_path)
    app = create_worker_app(settings)
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://worker")
    client = RemoteWorkerClient(
        "http://worker",
        "test-secret",
        http_client=http_client,
    )
    async with app.router.lifespan_context(app):
        result = await client.execute(
            "db_query",
            {"query": "SELECT order_id FROM orders LIMIT 1"},
            RiskClass.READ_ONLY,
            "call-1",
            "tenant-1",
        )
    await http_client.aclose()

    assert result["row_count"] == 1
