"""Bounded self-healing orchestration for tool execution."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from random import Random
from typing import Any, TypeVar

from gateway.errors import NeedsReviewError
from gateway.models import RepairFailure, RiskClass
from gateway.repair.backoff import BackoffPolicy
from gateway.repair.diagnose import FailureDiagnosis, diagnose

OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class RepairContext:
    """Tool properties needed to decide whether retries are safe."""

    risk_class: RiskClass
    idempotent: bool = False
    tool_name: str = "unknown"
    tool_schema: Mapping[str, Any] | None = None


Repairer = Callable[[Mapping[str, Any], FailureDiagnosis], Awaitable[Mapping[str, Any]]]
Executor = Callable[[Mapping[str, Any]], Awaitable[OutputT]]


class RepairLoop:
    """Execute a bounded repair sequence without bypassing policy."""

    def __init__(
        self,
        policy: BackoffPolicy | None = None,
        random: Random | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure retry limits and injectable timing dependencies."""

        self._policy = policy or BackoffPolicy()
        self._random = random or Random()
        self._sleep = sleep
        self._monotonic = monotonic

    async def run(
        self,
        arguments: Mapping[str, Any],
        executor: Executor[OutputT],
        context: RepairContext,
        repairer: Repairer | None = None,
    ) -> tuple[OutputT, int]:
        """Run execution and return its output plus the successful attempt number."""

        started = self._monotonic()
        current: Mapping[str, Any] = dict(arguments)
        attempt = 1
        while True:
            try:
                return await executor(current), attempt
            except BaseException as error:
                diagnosis = diagnose(error)
                if not self._can_retry(diagnosis, context, attempt, started):
                    if (
                        diagnosis.category
                        in {
                            RepairFailure.TIMEOUT,
                            RepairFailure.SANDBOX_CRASH,
                        }
                        and context.risk_class is not RiskClass.READ_ONLY
                    ):
                        raise NeedsReviewError(
                            "A mutating call may have partially executed "
                            "and needs review"
                        ) from error
                    raise
                if diagnosis.category in {
                    RepairFailure.TIMEOUT,
                    RepairFailure.SANDBOX_CRASH,
                }:
                    repaired = current
                else:
                    if repairer is None:
                        raise
                    repaired = await repairer(current, diagnosis)
                delay = self._policy.delay(attempt, self._random)
                if self._monotonic() + delay - started > self._policy.max_total_seconds:
                    raise
                await self._sleep(delay)
                current = dict(repaired)
                attempt += 1

    def _can_retry(
        self,
        diagnosis: FailureDiagnosis,
        context: RepairContext,
        attempt: int,
        started: float,
    ) -> bool:
        """Return whether a failure is eligible for another bounded attempt."""

        if diagnosis.category is RepairFailure.POLICY_DENIED:
            return False
        if attempt >= self._policy.max_attempts:
            return False
        if self._monotonic() - started >= self._policy.max_total_seconds:
            return False
        if context.risk_class is not RiskClass.READ_ONLY and not context.idempotent:
            return False
        return diagnosis.category in {
            RepairFailure.SCHEMA_MISMATCH,
            RepairFailure.MALFORMED_JSON,
            RepairFailure.MISSING_REQUIRED_FIELD,
            RepairFailure.TYPE_COERCION,
            RepairFailure.TIMEOUT,
            RepairFailure.SANDBOX_CRASH,
            RepairFailure.TOOL_ERROR,
        }
