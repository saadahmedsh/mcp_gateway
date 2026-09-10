"""Tests for lifecycle state persistence and degraded Redis behavior."""

from datetime import UTC

import pytest

from gateway.errors import StateStoreUnavailableError
from gateway.models import (
    AttemptRecord,
    RiskClass,
    StateTransition,
    ToolCallRecord,
    ToolCallState,
)
from gateway.state.redis_store import InMemoryStateStore, RedisStateStore, utc_now


def _record() -> ToolCallRecord:
    """Build a deterministic initial call record for store tests."""

    timestamp = utc_now()
    return ToolCallRecord(
        call_id="call-1",
        trace_id="trace-1",
        session_id="session-1",
        tool_name="db_query",
        risk_class=RiskClass.READ_ONLY,
        arguments={"query": "SELECT 1"},
        current_state=ToolCallState.RECEIVED,
        transitions=[
            StateTransition(state=ToolCallState.RECEIVED, timestamp=timestamp)
        ],
        attempts=[AttemptRecord(attempt=1, started_at=timestamp)],
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.asyncio
async def test_in_memory_store_persists_full_lifecycle() -> None:
    """Verify transitions, attempt outcomes, timestamps, and session indexing."""

    store = InMemoryStateStore()
    await store.create_call(_record())
    await store.transition("call-1", ToolCallState.VALIDATED)
    await store.transition("call-1", ToolCallState.POLICY_CHECKED)
    await store.transition("call-1", ToolCallState.EXECUTING)
    await store.transition("call-1", ToolCallState.SUCCEEDED)
    await store.finish_attempt("call-1", 1, "succeeded")

    record = await store.get_call("call-1")

    assert record is not None
    assert record.current_state is ToolCallState.SUCCEEDED
    assert [item.state for item in record.transitions] == [
        ToolCallState.RECEIVED,
        ToolCallState.VALIDATED,
        ToolCallState.POLICY_CHECKED,
        ToolCallState.EXECUTING,
        ToolCallState.SUCCEEDED,
    ]
    assert record.attempts[0].outcome == "succeeded"
    assert record.attempts[0].finished_at is not None
    assert record.created_at.tzinfo is UTC
    assert await store.list_session_calls("session-1") == ["call-1"]


@pytest.mark.asyncio
async def test_redis_unavailability_is_bounded_and_typed() -> None:
    """Verify an unreachable Redis endpoint returns a clean typed failure."""

    store = RedisStateStore("redis://127.0.0.1:6399/0", operation_timeout_seconds=0.1)

    with pytest.raises(StateStoreUnavailableError) as captured:
        await store.create_call(_record())

    assert captured.value.response.error.code == "state_store_unavailable"
    await store.close()
