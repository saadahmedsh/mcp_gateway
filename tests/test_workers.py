"""Execution-worker boundary tests."""

import asyncio
from datetime import UTC, datetime

import pytest

from gateway.errors import NeedsReviewError, ToolExecutionError
from gateway.models import RiskClass, StrictModel
from gateway.registry import ToolDefinition, ToolRegistry
from gateway.workers.models import WorkerJob
from gateway.workers.service import InProcessWorkerClient, WorkerService, _sign_job


class WorkerInput(StrictModel):
    """Input accepted by the test worker tool."""

    value: int


class WorkerOutput(StrictModel):
    """Output returned by the test worker tool."""

    value: int


def build_registry(
    active_counter: dict[str, int] | None = None,
    delay: float = 0.0,
    risk_class: RiskClass = RiskClass.READ_ONLY,
) -> ToolRegistry:
    """Build a registry with a controllable asynchronous test handler."""

    counter = (
        active_counter
        if active_counter is not None
        else {"active": 0, "max": 0, "calls": 0}
    )

    async def handler(request: WorkerInput) -> WorkerOutput:
        """Track concurrency and return the validated input."""

        counter["active"] += 1
        counter["calls"] += 1
        counter["max"] = max(counter["max"], counter["active"])
        try:
            await asyncio.sleep(delay)
            return WorkerOutput(value=request.value)
        finally:
            counter["active"] -= 1

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="worker_test",
            description="test worker",
            risk_class=risk_class,
            input_model=WorkerInput,
            output_model=WorkerOutput,
            handler=handler,
            idempotent=risk_class is RiskClass.READ_ONLY,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_worker_authentication_and_idempotency() -> None:
    """Reject forged jobs and execute duplicate idempotency keys once."""

    counter: dict[str, int] = {"active": 0, "max": 0, "calls": 0}
    service = WorkerService(build_registry(counter), "test-secret", max_concurrency=2)
    client = InProcessWorkerClient(service, "test-secret")
    await service.start()
    try:
        first, second = await asyncio.gather(
            client.execute(
                "worker_test",
                {"value": 7},
                RiskClass.READ_ONLY,
                "call-1",
                "tenant-1",
                "same-key",
            ),
            client.execute(
                "worker_test",
                {"value": 7},
                RiskClass.READ_ONLY,
                "call-2",
                "tenant-1",
                "same-key",
            ),
        )
        assert first == second == {"value": 7}
        assert counter["calls"] == 1
        forged = WorkerJob(
            job_id="forged",
            call_id="call-forged",
            tenant_id="tenant-1",
            tool_name="worker_test",
            risk_class=RiskClass.READ_ONLY,
            arguments={"value": 1},
            idempotency_key="forged-key",
            submitted_at=datetime.now(UTC),
            auth_token="bad-token",
        )
        with pytest.raises(ToolExecutionError, match="authentication"):
            await service.submit(forged)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_worker_concurrency_is_bounded() -> None:
    """Ensure the service never exceeds its configured concurrency."""

    counter: dict[str, int] = {"active": 0, "max": 0, "calls": 0}
    service = WorkerService(
        build_registry(counter, delay=0.02),
        "test-secret",
        max_concurrency=2,
    )
    client = InProcessWorkerClient(service, "test-secret")
    await service.start()
    try:
        results = await asyncio.gather(
            *[
                client.execute(
                    "worker_test",
                    {"value": index},
                    RiskClass.READ_ONLY,
                    f"call-{index}",
                    "tenant-1",
                    f"key-{index}",
                )
                for index in range(6)
            ]
        )
        assert [item["value"] for item in results] == list(range(6))
        assert counter["max"] <= 2
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_stopping_worker_marks_mutation_for_review() -> None:
    """Never report success when a mutating job is interrupted."""

    service = WorkerService(
        build_registry(delay=1.0, risk_class=RiskClass.MUTATING),
        "test-secret",
        max_concurrency=1,
    )
    client = InProcessWorkerClient(service, "test-secret")
    await service.start()
    task = asyncio.create_task(
        client.execute(
            "worker_test",
            {"value": 4},
            RiskClass.MUTATING,
            "call-mutation",
            "tenant-1",
            "mutation-key",
        )
    )
    await asyncio.sleep(0.05)
    await service.stop()
    with pytest.raises(NeedsReviewError):
        await task
    assert len(service.reconciliation) == 1


@pytest.mark.asyncio
async def test_worker_circuit_breaker_blocks_repeated_failures() -> None:
    """Open the circuit after repeated worker failures."""

    registry = ToolRegistry()

    async def failing_handler(_request: WorkerInput) -> WorkerOutput:
        """Raise a deterministic worker failure."""

        raise ToolExecutionError("synthetic worker failure")

    registry.register(
        ToolDefinition(
            name="worker_test",
            description="test worker",
            risk_class=RiskClass.READ_ONLY,
            input_model=WorkerInput,
            output_model=WorkerOutput,
            handler=failing_handler,
        )
    )
    service = WorkerService(
        registry,
        "test-secret",
        max_concurrency=1,
        failure_threshold=2,
        reset_timeout_seconds=60.0,
    )
    client = InProcessWorkerClient(service, "test-secret")
    await service.start()
    try:
        for index in range(2):
            with pytest.raises(ToolExecutionError, match="synthetic worker failure"):
                await client.execute(
                    "worker_test",
                    {"value": index},
                    RiskClass.READ_ONLY,
                    f"failure-call-{index}",
                    "tenant-1",
                    f"failure-key-{index}",
                )
        with pytest.raises(ToolExecutionError, match="circuit is open"):
            await client.execute(
                "worker_test",
                {"value": 3},
                RiskClass.READ_ONLY,
                "failure-call-3",
                "tenant-1",
                "failure-key-3",
            )
    finally:
        await service.stop()


def test_job_signature_is_deterministic() -> None:
    """Ensure identical job payloads produce the same authentication token."""

    job = WorkerJob(
        job_id="job-1",
        call_id="call-1",
        tenant_id="tenant-1",
        tool_name="worker_test",
        risk_class=RiskClass.READ_ONLY,
        arguments={"value": 1},
        idempotency_key="key-1",
        submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        auth_token="",
    )
    assert _sign_job(job, "test-secret") == _sign_job(job, "test-secret")
