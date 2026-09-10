"""Tests for identity propagation into policy and lifecycle state."""

from typing import Any

import pytest
from mcp import types

from gateway.models import PolicyDecision, Principal, RiskClass, Role, StrictModel
from gateway.policy.decisions import ALLOW
from gateway.registry import ToolDefinition, ToolRegistry
from gateway.server import execute_tool_call
from gateway.state.redis_store import InMemoryStateStore
from gateway.tracing.otel import create_tracing


class IdentityInput(StrictModel):
    """Input for the identity propagation test tool."""

    value: str


class IdentityOutput(StrictModel):
    """Output for the identity propagation test tool."""

    value: str


class CapturingPolicy:
    """Policy adapter that records the principal supplied by dispatch."""

    def __init__(self) -> None:
        """Initialize an empty input capture."""

        self.input: dict[str, Any] | None = None

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Capture the policy input and allow the call."""

        self.input = policy_input
        return PolicyDecision(
            outcome=ALLOW,
            matched_rule="test_allow",
            reason="test",
        )


@pytest.mark.asyncio
async def test_principal_reaches_policy_and_state() -> None:
    """Verify verified identity is present in policy input and state records."""

    registry = ToolRegistry()

    async def handler(request: IdentityInput) -> IdentityOutput:
        """Return the supplied value."""

        return IdentityOutput(value=request.value)

    registry.register(
        ToolDefinition(
            name="identity_tool",
            description="Identity test tool",
            risk_class=RiskClass.READ_ONLY,
            input_model=IdentityInput,
            output_model=IdentityOutput,
            handler=handler,
        )
    )
    policy = CapturingPolicy()
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles=[Role.USER],
        issuer="https://issuer.example",
    )
    store = InMemoryStateStore()
    tracing = create_tracing()

    result = await execute_tool_call(
        registry,
        store,
        tracing,
        "session",
        types.CallToolRequestParams(
            name="identity_tool",
            arguments={"value": "ok"},
        ),
        policy,
        principal=principal,
    )

    assert result.is_error is False
    assert policy.input is not None
    assert policy.input["principal"] == principal.model_dump(mode="json")
    call_ids = await store.list_session_calls("session")
    assert len(call_ids) == 1
    call = await store.get_call(call_ids[0])
    assert call is not None
    assert call.principal == principal
    await store.close()
    tracing.shutdown()
