"""Control-plane persistence tests and optional PostgreSQL integration checks."""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from gateway.control_plane.models import ApprovalModel, Base, TenantModel, ToolCallModel
from gateway.control_plane.repository import ControlPlaneRepository
from gateway.models import (
    ApprovalRequest,
    AttemptRecord,
    Principal,
    RiskClass,
    Role,
    StateTransition,
    ToolCallRecord,
    ToolCallState,
)

TEST_DATABASE_URL = os.environ.get("GATEWAY_CONTROL_PLANE_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason=(
        "set GATEWAY_CONTROL_PLANE_TEST_DATABASE_URL for PostgreSQL integration tests"
    ),
)


def test_metadata_declares_all_control_plane_tables() -> None:
    """Verify that the declarative schema contains each durable table."""

    assert set(Base.metadata.tables) == {
        "tenants",
        "principals",
        "tool_calls",
        "attempts",
        "approvals",
        "idempotency_keys",
        "policy_versions",
        "configurations",
    }
    assert TenantModel.__tablename__ == "tenants"
    assert ApprovalModel.__tablename__ == "approvals"
    assert ToolCallModel.__tablename__ == "tool_calls"


@pytest.fixture
async def repository() -> AsyncGenerator[ControlPlaneRepository, None]:
    """Provide a PostgreSQL repository for integration tests."""

    if TEST_DATABASE_URL is None:
        pytest.skip("PostgreSQL integration URL is not configured")
    value = ControlPlaneRepository(TEST_DATABASE_URL)
    await value.create_schema()
    yield value
    await value.close()


@requires_database
@pytest.mark.asyncio
async def test_tenant_principal_and_approval_are_persisted(
    repository: ControlPlaneRepository,
) -> None:
    """Verify tenant, principal, and approval records survive transactions."""

    principal = Principal(
        subject="operator@example.com",
        tenant_id="tenant-test",
        roles=[Role.OPERATOR],
        issuer="https://issuer.example.test",
        agent_id="agent-test",
    )
    request = ApprovalRequest(
        approval_id="approval-test",
        call_id="call-test",
        tool_name="shell_exec",
        arguments={"command": "printf test"},
        policy_input={"risk_class": "destructive"},
        matched_rule="destructive_risk_class",
        created_at=datetime.now(UTC),
    )
    await repository.upsert_tenant("tenant-test", "Synthetic tenant")
    await repository.upsert_principal(principal)
    await repository.create_approval(request, tenant_id="tenant-test")
    assert await repository.resolve_approval(
        "approval-test",
        "approved",
        "operator@example.com",
        "Reviewed",
        datetime.now(UTC),
    )


@requires_database
@pytest.mark.asyncio
async def test_tool_call_attempts_and_idempotency_are_durable(
    repository: ControlPlaneRepository,
) -> None:
    """Verify execution history and idempotency records are persisted."""

    await repository.upsert_tenant("tenant-test", "Synthetic tenant")
    now = datetime.now(UTC)
    record = ToolCallRecord(
        call_id="call-test",
        trace_id="trace-test",
        session_id="session-test",
        tool_name="db_query",
        risk_class=RiskClass.READ_ONLY,
        arguments={"query": "SELECT 1"},
        current_state=ToolCallState.SUCCEEDED,
        transitions=[StateTransition(state=ToolCallState.SUCCEEDED, timestamp=now)],
        attempts=[
            AttemptRecord(
                attempt=1,
                started_at=now,
                finished_at=now,
                outcome="succeeded",
            )
        ],
        created_at=now,
        updated_at=now,
        principal=Principal(
            subject="user@example.com", tenant_id="tenant-test", roles=[Role.USER]
        ),
    )
    await repository.upsert_tool_call(record)
    await repository.put_idempotency_key(
        "tenant-test", "request-1", "call-test", "succeeded"
    )
    await repository.record_policy_version(
        "policy-v1", "abc123", "oci://registry/policy:v1", signed=True
    )
    await repository.set_configuration("sandbox", {"runtime": "hardened-docker"})
    stored = await repository.get_idempotency_key("tenant-test", "request-1")
    attempts = await repository.list_attempts("call-test")
    assert stored is not None
    assert stored.call_id == "call-test"
    assert len(attempts) == 1
    assert attempts[0].outcome == "succeeded"


@requires_database
@pytest.mark.asyncio
async def test_transaction_rolls_back_on_failure(
    repository: ControlPlaneRepository,
) -> None:
    """Verify failed transactions leave no partially committed tenant."""

    with pytest.raises(RuntimeError, match="rollback"):
        async with repository._session_factory.begin() as session:
            session.add(TenantModel(id="rolled-back", name="Should not persist"))
            raise RuntimeError("rollback")

    async with repository._session_factory() as session:
        assert await session.get(TenantModel, "rolled-back") is None
