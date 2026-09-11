"""Tests for gateway-to-worker HTTP submission and authentication."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from gateway.config import Settings
from gateway.errors import NeedsReviewError
from gateway.models import RiskClass
from gateway.server import create_execution_runtime
from gateway.workers.models import WorkerResult
from gateway.workers.remote import RemoteWorkerClient


@pytest.mark.asyncio
async def test_remote_client_signs_and_submits_job() -> None:
    """Verify the worker receives a valid HMAC-signed request."""

    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        """Capture one request and return a successful worker result."""

        payload = dict(json.loads(request.content))
        seen.update(payload)
        return httpx.Response(
            200,
            json=WorkerResult(
                job_id=str(payload["job_id"]),
                outcome="succeeded",
                output={"row_count": 1},
                completed_at=datetime.now(UTC),
            ).model_dump(mode="json"),
        )

    client = RemoteWorkerClient(
        "http://worker:8090",
        "test-secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await client.execute(
        "db_query",
        {"query": "SELECT 1"},
        RiskClass.READ_ONLY,
        "call-1",
        "tenant-1",
    )

    assert result == {"row_count": 1}
    assert seen["tool_name"] == "db_query"
    assert seen["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_remote_client_surfaces_mutation_transport_failure() -> None:
    """Verify uncertain remote mutations require reconciliation review."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        """Raise a transport error for the submitted job."""

        raise httpx.ConnectError("worker unavailable")

    client = RemoteWorkerClient(
        "http://worker:8090",
        "test-secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(NeedsReviewError, match="mutation outcome requires review"):
        await client.execute(
            "shell_exec",
            {"command": "printf safe"},
            RiskClass.DESTRUCTIVE,
            "call-1",
            "tenant-1",
        )


def test_remote_runtime_requires_endpoint_and_secret(tmp_path: Path) -> None:
    """Reject a remote gateway configuration that cannot authenticate."""

    settings = Settings(
        environment="test",
        database_path=tmp_path / "gateway.sqlite",
        audit_log_path=tmp_path / "audit.jsonl",
        worker_mode="remote",
        worker_url=AnyHttpUrl("http://worker:8090"),
        worker_shared_secret=SecretStr("test-secret"),
    )
    registry, service, remote = create_execution_runtime(settings, None)
    assert registry.list_tools()
    assert service is None
    assert remote is not None
