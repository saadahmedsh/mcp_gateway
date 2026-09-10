"""Tests for bounded failure diagnosis and self-healing retries."""

from collections.abc import Mapping
from random import Random
from typing import Any

import pytest

from gateway.errors import (
    NeedsReviewError,
    PolicyDeniedError,
    SandboxTimeoutError,
    ToolExecutionError,
)
from gateway.models import RepairFailure, RiskClass
from gateway.repair.backoff import BackoffPolicy
from gateway.repair.diagnose import diagnose
from gateway.repair.loop import RepairContext, RepairLoop


@pytest.mark.asyncio
async def test_malformed_arguments_repair_and_succeed() -> None:
    """Repair a schema failure and succeed on the next attempt."""

    attempts = 0

    async def execute(arguments: Mapping[str, Any]) -> str:
        nonlocal attempts
        attempts += 1
        if arguments.get("value") != "fixed":
            raise ToolExecutionError("schema validation failed")
        return "ok"

    async def repair(
        _arguments: Mapping[str, Any], diagnosis: Any
    ) -> Mapping[str, Any]:
        assert diagnosis.category is RepairFailure.TOOL_ERROR
        return {"value": "fixed"}

    result, attempt = await RepairLoop(
        BackoffPolicy(base_seconds=0.0, jitter_ratio=0.0), Random(1)
    ).run(
        {"value": "bad"},
        execute,
        RepairContext(RiskClass.READ_ONLY),
        repair,
    )
    assert result == "ok"
    assert attempt == 2
    assert attempts == 2


@pytest.mark.asyncio
async def test_timeout_retries_read_only_call() -> None:
    """Retry a transient timeout for a read-only operation."""

    attempts = 0

    async def execute(_arguments: Mapping[str, Any]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SandboxTimeoutError()
        return "ok"

    result, attempt = await RepairLoop(
        BackoffPolicy(base_seconds=0.0, jitter_ratio=0.0), Random(1)
    ).run({}, execute, RepairContext(RiskClass.READ_ONLY))
    assert result == "ok"
    assert attempt == 2


@pytest.mark.asyncio
async def test_policy_denial_is_never_retried() -> None:
    """Never retry a policy denial."""

    attempts = 0

    async def execute(_arguments: Mapping[str, Any]) -> str:
        nonlocal attempts
        attempts += 1
        raise PolicyDeniedError("denied")

    with pytest.raises(PolicyDeniedError):
        await RepairLoop().run({}, execute, RepairContext(RiskClass.READ_ONLY))
    assert attempts == 1


@pytest.mark.asyncio
async def test_non_idempotent_mutation_needs_review() -> None:
    """Do not retry a timeout that may have partially applied a mutation."""

    async def execute(_arguments: Mapping[str, Any]) -> str:
        raise SandboxTimeoutError()

    with pytest.raises(NeedsReviewError, match="needs review"):
        await RepairLoop().run({}, execute, RepairContext(RiskClass.MUTATING))


def test_backoff_is_bounded_and_deterministic() -> None:
    """Verify exponential delays are capped and reproducible."""

    policy = BackoffPolicy(
        base_seconds=1.0,
        max_seconds=3.0,
        jitter_ratio=0.0,
    )
    random = Random(4)
    assert policy.delay(1, random) == 1.0
    assert policy.delay(2, random) == 2.0
    assert policy.delay(3, random) == 3.0
    assert policy.delay(4, random) == 3.0


def test_failure_diagnosis_classifies_timeout() -> None:
    """Classify a sandbox timeout into the timeout repair strategy."""

    assert diagnose(SandboxTimeoutError()).category is RepairFailure.TIMEOUT
