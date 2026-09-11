"""HTTP client for the separately deployed execution worker service."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from gateway.errors import NeedsReviewError, ToolExecutionError
from gateway.models import RiskClass
from gateway.workers.models import WorkerJob, WorkerResult
from gateway.workers.service import _sign_job


class RemoteWorkerClient:
    """Submit signed jobs to a worker service over authenticated HTTP."""

    def __init__(
        self,
        base_url: str,
        shared_secret: str,
        timeout_seconds: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client with an optional injected HTTP transport for tests."""

        self._base_url = base_url.rstrip("/")
        self._shared_secret = shared_secret
        self._timeout = timeout_seconds
        self._client = http_client
        self._owns_client = http_client is None

    def _job(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk_class: RiskClass,
        call_id: str,
        tenant_id: str,
        idempotency_key: str | None,
    ) -> WorkerJob:
        """Build and sign one worker job payload."""

        job = WorkerJob(
            job_id=uuid4().hex,
            call_id=call_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            risk_class=risk_class,
            arguments=arguments,
            idempotency_key=idempotency_key or f"{call_id}:{tool_name}",
            submitted_at=datetime.now(UTC),
            auth_token="",
        )
        return job.model_copy(
            update={"auth_token": _sign_job(job, self._shared_secret)}
        )

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk_class: RiskClass,
        call_id: str,
        tenant_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit one job and return its worker-produced output."""

        job = self._job(
            tool_name,
            arguments,
            risk_class,
            call_id,
            tenant_id,
            idempotency_key,
        )
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
            self._client = client
        try:
            response = await client.post(
                f"{self._base_url}/jobs",
                json=job.model_dump(mode="json"),
            )
        except (httpx.HTTPError, TimeoutError) as error:
            if risk_class is not RiskClass.READ_ONLY:
                raise NeedsReviewError(
                    "Worker transport failed; mutation outcome requires review"
                ) from error
            raise ToolExecutionError("Execution worker is unavailable") from error
        if response.status_code >= 400:
            raise ToolExecutionError(
                f"Execution worker returned HTTP {response.status_code}"
            )
        try:
            result = WorkerResult.model_validate_json(response.content)
        except (ValidationError, ValueError, TypeError) as error:
            raise ToolExecutionError(
                "Execution worker returned invalid JSON"
            ) from error
        if result.outcome != "succeeded":
            if result.error_code == "needs_review":
                raise NeedsReviewError(
                    result.error_message or "Worker outcome requires review"
                )
            raise ToolExecutionError(result.error_message or "Execution worker failed")
        if result.output is None:
            raise ToolExecutionError("Execution worker returned no output")
        return result.output

    async def healthcheck(self) -> bool:
        """Check whether the remote worker endpoint is accepting requests."""

        client = self._client
        if client is None:
            async with httpx.AsyncClient(timeout=min(self._timeout, 2.0)) as probe:
                try:
                    response = await probe.get(f"{self._base_url}/readyz")
                except (httpx.HTTPError, TimeoutError):
                    return False
                return response.status_code == 200
        try:
            response = await client.get(f"{self._base_url}/readyz")
        except (httpx.HTTPError, TimeoutError):
            return False
        return response.status_code == 200

    async def close(self) -> None:
        """Close the owned HTTP client."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["RemoteWorkerClient"]
