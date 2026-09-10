"""Tests for deterministic policy and human approval behavior."""

import asyncio
from typing import Any

import pytest
from mcp import types

from gateway.errors import PolicyUnavailableError
from gateway.hitl.queue import InMemoryApprovalQueue
from gateway.models import PolicyDecision, RiskClass, StrictModel, ToolCallState
from gateway.policy.client import OpaPolicyClient
from gateway.registry import ToolDefinition, ToolRegistry
from gateway.server import execute_tool_call
from gateway.state.redis_store import InMemoryStateStore
from gateway.tracing.otel import create_tracing


class MutatingInput(StrictModel):
    """Input for a test-only mutating tool."""

    value: str


class MutatingOutput(StrictModel):
    """Output for a test-only mutating tool."""

    value: str


class RequiresApprovalPolicy:
    """Policy adapter that always requests human approval."""

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Request approval for the supplied call."""

        del policy_input
        return PolicyDecision(
            outcome="requires_approval",
            matched_rule="test_requires_approval",
            reason="test approval required",
        )


def create_mutating_registry(executed: list[str]) -> ToolRegistry:
    """Create a registry containing one observable mutating tool."""

    async def handler(request: MutatingInput) -> MutatingOutput:
        """Record execution and return the supplied value."""

        executed.append(request.value)
        return MutatingOutput(value=request.value)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="mutate",
            description="Test mutating tool",
            risk_class=RiskClass.MUTATING,
            input_model=MutatingInput,
            output_model=MutatingOutput,
            handler=handler,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_approval_executes_after_operator_decision() -> None:
    """Verify approval suspends execution and then permits the tool."""

    executed: list[str] = []
    store = InMemoryStateStore()
    queue = InMemoryApprovalQueue()
    tracing = create_tracing()
    task = asyncio.create_task(
        execute_tool_call(
            create_mutating_registry(executed),
            store,
            tracing,
            "session",
            types.CallToolRequestParams(name="mutate", arguments={"value": "ok"}),
            RequiresApprovalPolicy(),
            queue,
            1.0,
        )
    )
    await asyncio.sleep(0)
    pending = await queue.list_pending()
    assert len(pending) == 1
    await queue.decide(pending[0].approval_id, True, "operator", "approved")
    result = await task
    assert result.is_error is False
    assert executed == ["ok"]
    record = await store.get_call(pending[0].call_id)
    assert record is not None
    assert record.current_state is ToolCallState.SUCCEEDED
    await store.close()
    tracing.shutdown()


@pytest.mark.asyncio
async def test_rejection_prevents_execution() -> None:
    """Verify rejected approvals return denial and never execute the tool."""

    executed: list[str] = []
    store = InMemoryStateStore()
    queue = InMemoryApprovalQueue()
    tracing = create_tracing()
    task = asyncio.create_task(
        execute_tool_call(
            create_mutating_registry(executed),
            store,
            tracing,
            "session",
            types.CallToolRequestParams(name="mutate", arguments={"value": "no"}),
            RequiresApprovalPolicy(),
            queue,
            1.0,
        )
    )
    await asyncio.sleep(0)
    pending = await queue.list_pending()
    await queue.decide(pending[0].approval_id, False, "operator", "rejected")
    result = await task
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "approval_rejected"
    assert executed == []
    await store.close()
    tracing.shutdown()


@pytest.mark.asyncio
async def test_approval_timeout_denies() -> None:
    """Verify an unresolved request becomes a denial after its timeout."""

    store = InMemoryStateStore()
    queue = InMemoryApprovalQueue()
    tracing = create_tracing()
    result = await execute_tool_call(
        create_mutating_registry([]),
        store,
        tracing,
        "session",
        types.CallToolRequestParams(name="mutate", arguments={"value": "late"}),
        RequiresApprovalPolicy(),
        queue,
        0.01,
    )
    assert result.structured_content["error"]["code"] == "approval_timeout"
    await store.close()
    tracing.shutdown()


@pytest.mark.asyncio
async def test_opa_client_fails_closed_on_unreachable_endpoint() -> None:
    """Verify an unreachable OPA endpoint becomes a typed policy error."""

    client = OpaPolicyClient("http://127.0.0.1:1", timeout_seconds=0.01)
    with pytest.raises(PolicyUnavailableError) as raised:
        await client.evaluate({"tool_name": "db_query"})
    assert "policy" in str(raised.value).lower()
