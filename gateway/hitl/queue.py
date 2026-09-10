"""Redis-backed human approval queue with bounded polling."""

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from gateway.errors import (
    ApprovalRejectedError,
    ApprovalTimeoutError,
    StateStoreUnavailableError,
)
from gateway.models import ApprovalRequest


class ApprovalQueue(Protocol):
    """Interface for submitting and resolving pending approvals."""

    async def submit(self, request: ApprovalRequest) -> None:
        """Persist a pending approval."""

    async def wait(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        """Wait for an approval decision."""

    async def decide(
        self, approval_id: str, approved: bool, approver: str, reason: str
    ) -> None:
        """Record an operator decision."""

    async def list_pending(self) -> list[ApprovalRequest]:
        """List pending approvals."""

    async def close(self) -> None:
        """Release queue resources."""


class RedisApprovalQueue:
    """Store approval requests and decisions in Redis JSON values."""

    def __init__(
        self,
        redis_url: str,
        poll_interval_seconds: float = 0.2,
        ttl_seconds: int = 86_400,
    ) -> None:
        """Create a queue using the configured Redis connection."""

        self._client: Redis = Redis.from_url(redis_url, decode_responses=True)
        self._poll_interval = poll_interval_seconds
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(approval_id: str) -> str:
        """Build the Redis key for one approval request."""

        return f"gateway:approval:{approval_id}"

    async def submit(self, request: ApprovalRequest) -> None:
        """Persist a pending approval request."""

        try:
            await self._client.set(
                self._key(request.approval_id),
                request.model_dump_json(),
                ex=self._ttl_seconds,
            )
        except (RedisError, OSError) as error:
            raise StateStoreUnavailableError() from error

    async def wait(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        """Poll until an approver resolves the request or timeout expires."""

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    raw = await self._client.get(self._key(approval_id))
                    if raw is not None:
                        request = ApprovalRequest.model_validate_json(raw)
                        if request.decision is not None:
                            if request.decision != "approved":
                                raise ApprovalRejectedError(
                                    request.reason or "The approval was rejected"
                                )
                            return request
                    await asyncio.sleep(self._poll_interval)
        except ApprovalTimeoutError:
            raise
        except TimeoutError as error:
            raise ApprovalTimeoutError() from error
        except (RedisError, OSError) as error:
            raise StateStoreUnavailableError() from error

    async def decide(
        self, approval_id: str, approved: bool, approver: str, reason: str
    ) -> None:
        """Record an approval or rejection with operator attribution."""

        if not reason.strip():
            raise ApprovalRejectedError("An approval or rejection requires a reason")
        try:
            raw = await self._client.get(self._key(approval_id))
            if raw is None:
                raise ApprovalTimeoutError()
            request = ApprovalRequest.model_validate_json(raw)
            updated = request.model_copy(
                update={
                    "decision": "approved" if approved else "rejected",
                    "approver": approver,
                    "reason": reason,
                }
            )
            await self._client.set(
                self._key(approval_id),
                updated.model_dump_json(),
                ex=self._ttl_seconds,
            )
        except ApprovalTimeoutError:
            raise
        except (RedisError, OSError) as error:
            raise StateStoreUnavailableError() from error

    async def list_pending(self) -> list[ApprovalRequest]:
        """Return unresolved approval requests."""

        requests: list[ApprovalRequest] = []
        try:
            async for key in self._client.scan_iter(match="gateway:approval:*"):
                raw = await self._client.get(key)
                if raw is not None:
                    request = ApprovalRequest.model_validate_json(raw)
                    if request.decision is None:
                        requests.append(request)
        except (RedisError, OSError) as error:
            raise StateStoreUnavailableError() from error
        return requests

    async def close(self) -> None:
        """Close the Redis connection."""

        await self._client.aclose()


class InMemoryApprovalQueue:
    """Deterministic queue implementation for policy and HITL tests."""

    def __init__(self) -> None:
        """Initialize an empty approval collection."""

        self._requests: dict[str, ApprovalRequest] = {}
        self._condition = asyncio.Condition()

    async def submit(self, request: ApprovalRequest) -> None:
        """Store a pending request."""

        async with self._condition:
            self._requests[request.approval_id] = request
            self._condition.notify_all()

    async def wait(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        """Wait for a decision in memory."""

        async def resolved() -> ApprovalRequest | None:
            request = self._requests.get(approval_id)
            return request if request and request.decision else None

        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._condition:
                    while (request := await resolved()) is None:
                        await self._condition.wait()
                    if request.decision != "approved":
                        raise ApprovalRejectedError(
                            request.reason or "The approval was rejected"
                        )
                    return request
        except TimeoutError as error:
            raise ApprovalTimeoutError() from error

    async def decide(
        self, approval_id: str, approved: bool, approver: str, reason: str
    ) -> None:
        """Resolve one pending request."""

        if not reason.strip():
            raise ApprovalRejectedError("An approval or rejection requires a reason")
        async with self._condition:
            request = self._requests.get(approval_id)
            if request is None:
                raise ApprovalTimeoutError()
            self._requests[approval_id] = request.model_copy(
                update={
                    "decision": "approved" if approved else "rejected",
                    "approver": approver,
                    "reason": reason,
                }
            )
            self._condition.notify_all()

    async def list_pending(self) -> list[ApprovalRequest]:
        """Return unresolved in-memory requests."""

        return [item for item in self._requests.values() if item.decision is None]

    async def close(self) -> None:
        """Release no-op in-memory resources."""


def create_approval_request(
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    policy_input: dict[str, Any],
    matched_rule: str,
) -> ApprovalRequest:
    """Create a new approval request with a unique identifier."""

    return ApprovalRequest(
        approval_id=uuid4().hex,
        call_id=call_id,
        tool_name=tool_name,
        arguments=arguments,
        policy_input=policy_input,
        matched_rule=matched_rule,
        created_at=datetime.now(UTC),
    )
