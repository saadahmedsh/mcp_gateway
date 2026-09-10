"""Async Redis and in-memory stores for tool-call lifecycle state."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

from gateway.errors import StateStoreUnavailableError
from gateway.models import (
    StateTransition,
    ToolCallRecord,
    ToolCallState,
)


class StateStore(Protocol):
    """Interface used by the gateway lifecycle orchestration."""

    async def create_call(self, record: ToolCallRecord) -> None:
        """Persist a new call and index it under its session."""

    async def transition(
        self, call_id: str, state: ToolCallState, error: str | None = None
    ) -> None:
        """Append a lifecycle transition to an existing call."""

    async def finish_attempt(
        self, call_id: str, attempt: int, outcome: str, error: str | None = None
    ) -> None:
        """Complete one recorded execution attempt."""

    async def get_call(self, call_id: str) -> ToolCallRecord | None:
        """Read one call record by identifier."""

    async def list_session_calls(self, session_id: str) -> list[str]:
        """Return call identifiers associated with one session."""

    async def close(self) -> None:
        """Release resources owned by the store."""


def utc_now() -> datetime:
    """Return an explicitly timezone-aware UTC timestamp."""

    return datetime.now(UTC)


class RedisStateStore:
    """Persist call records in Redis with bounded operations and optimistic writes."""

    def __init__(
        self,
        redis_url: str,
        operation_timeout_seconds: float = 2.0,
        ttl_seconds: int = 86_400,
    ) -> None:
        """Create a Redis-backed store without connecting until the first operation."""

        self._client: Redis = Redis.from_url(redis_url, decode_responses=True)
        self._timeout = operation_timeout_seconds
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _call_key(call_id: str) -> str:
        """Build the Redis key for one call record."""

        return f"gateway:tool_call:{call_id}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        """Build the Redis list key for one session's call identifiers."""

        return f"gateway:session:{session_id}:calls"

    async def create_call(self, record: ToolCallRecord) -> None:
        """Atomically create a call record and append it to its session index."""

        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.pipeline(transaction=True) as pipeline:
                    pipeline.set(
                        self._call_key(record.call_id),
                        record.model_dump_json(),
                        ex=self._ttl_seconds,
                    )
                    pipeline.rpush(self._session_key(record.session_id), record.call_id)
                    pipeline.expire(
                        self._session_key(record.session_id), self._ttl_seconds
                    )
                    await pipeline.execute()
        except (RedisError, OSError, TimeoutError) as error:
            raise StateStoreUnavailableError() from error

    async def _update(
        self,
        call_id: str,
        updater: Callable[[ToolCallRecord], None],
    ) -> None:
        """Apply one atomic optimistic update to a stored record."""

        key = self._call_key(call_id)
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    async with self._client.pipeline(transaction=True) as pipeline:
                        await pipeline.watch(key)
                        raw = await pipeline.get(key)
                        if raw is None:
                            raise StateStoreUnavailableError()
                        record = ToolCallRecord.model_validate_json(cast(str, raw))
                        updater(record)
                        cast(Any, pipeline).multi()
                        pipeline.set(
                            key,
                            record.model_dump_json(),
                            ex=self._ttl_seconds,
                        )
                        try:
                            await pipeline.execute()
                            return
                        except WatchError:
                            continue
        except StateStoreUnavailableError:
            raise
        except (RedisError, OSError, TimeoutError) as error:
            raise StateStoreUnavailableError() from error

    async def transition(
        self, call_id: str, state: ToolCallState, error: str | None = None
    ) -> None:
        """Append a state transition and update the current state."""

        def update(record: ToolCallRecord) -> None:
            timestamp = utc_now()
            record.current_state = state
            record.updated_at = timestamp
            record.transitions.append(
                StateTransition(state=state, timestamp=timestamp, error=error)
            )

        await self._update(call_id, update)

    async def finish_attempt(
        self, call_id: str, attempt: int, outcome: str, error: str | None = None
    ) -> None:
        """Record the completion time and outcome for one attempt."""

        def update(record: ToolCallRecord) -> None:
            for item in reversed(record.attempts):
                if item.attempt == attempt:
                    item.finished_at = utc_now()
                    item.outcome = outcome
                    item.error = error
                    record.updated_at = utc_now()
                    return
            raise StateStoreUnavailableError()

        await self._update(call_id, update)

    async def get_call(self, call_id: str) -> ToolCallRecord | None:
        """Read and validate one Redis call record."""

        try:
            async with asyncio.timeout(self._timeout):
                raw = await self._client.get(self._call_key(call_id))
        except (RedisError, OSError, TimeoutError) as error:
            raise StateStoreUnavailableError() from error
        if raw is None:
            return None
        return ToolCallRecord.model_validate_json(cast(str, raw))

    async def list_session_calls(self, session_id: str) -> list[str]:
        """Read call identifiers in registration order for one session."""

        try:
            async with asyncio.timeout(self._timeout):
                values = await self._client.lrange(self._session_key(session_id), 0, -1)
        except (RedisError, OSError, TimeoutError) as error:
            raise StateStoreUnavailableError() from error
        return [str(value) for value in values]

    async def close(self) -> None:
        """Close the underlying asynchronous Redis client."""

        await self._client.aclose()


class InMemoryStateStore:
    """Deterministic state store used by unit tests and local isolated runs."""

    def __init__(self) -> None:
        """Initialize empty call and session collections."""

        self._calls: dict[str, ToolCallRecord] = {}
        self._sessions: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def create_call(self, record: ToolCallRecord) -> None:
        """Store a call and index its identifier under the session."""

        async with self._lock:
            self._calls[record.call_id] = record.model_copy(deep=True)
            self._sessions.setdefault(record.session_id, []).append(record.call_id)

    async def _update(
        self,
        call_id: str,
        updater: Callable[[ToolCallRecord], None],
    ) -> None:
        """Apply a serialized update to an in-memory record."""

        async with self._lock:
            record = self._calls.get(call_id)
            if record is None:
                raise StateStoreUnavailableError()
            updater(record)

    async def transition(
        self, call_id: str, state: ToolCallState, error: str | None = None
    ) -> None:
        """Append a state transition to an in-memory call."""

        def update(record: ToolCallRecord) -> None:
            timestamp = utc_now()
            record.current_state = state
            record.updated_at = timestamp
            record.transitions.append(
                StateTransition(state=state, timestamp=timestamp, error=error)
            )

        await self._update(call_id, update)

    async def finish_attempt(
        self, call_id: str, attempt: int, outcome: str, error: str | None = None
    ) -> None:
        """Complete one attempt in an in-memory call."""

        def update(record: ToolCallRecord) -> None:
            for item in reversed(record.attempts):
                if item.attempt == attempt:
                    item.finished_at = utc_now()
                    item.outcome = outcome
                    item.error = error
                    record.updated_at = utc_now()
                    return
            raise StateStoreUnavailableError()

        await self._update(call_id, update)

    async def get_call(self, call_id: str) -> ToolCallRecord | None:
        """Return a deep copy of an in-memory call record."""

        async with self._lock:
            record = self._calls.get(call_id)
            return None if record is None else record.model_copy(deep=True)

    async def list_session_calls(self, session_id: str) -> list[str]:
        """Return a copy of the call identifiers for one session."""

        async with self._lock:
            return list(self._sessions.get(session_id, []))

    async def close(self) -> None:
        """Release the in-memory store without external resources."""


def create_state_store(
    backend: str,
    redis_url: str,
    operation_timeout_seconds: float,
    ttl_seconds: int,
) -> StateStore:
    """Create the configured durable or test state-store implementation."""

    if backend == "memory":
        return InMemoryStateStore()
    return RedisStateStore(
        redis_url,
        operation_timeout_seconds=operation_timeout_seconds,
        ttl_seconds=ttl_seconds,
    )
