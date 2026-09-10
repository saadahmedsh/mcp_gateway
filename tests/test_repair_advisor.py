"""Tests for model-guided argument repair and policy re-evaluation."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from mcp import types

from gateway.config import Settings
from gateway.models import PolicyDecision, RepairFailure
from gateway.policy.decisions import ALLOW, DENY
from gateway.repair.advisor import LLMRepairAdvisor
from gateway.repair.diagnose import FailureDiagnosis
from gateway.server import create_registry, execute_tool_call
from gateway.state.redis_store import InMemoryStateStore
from gateway.tools.db_query import seed_database
from gateway.tracing.otel import create_tracing


class _Response:
    """Minimal HTTP response test double."""

    def raise_for_status(self) -> None:
        """Report a successful response."""

    def json(self) -> dict[str, Any]:
        """Return one model-generated argument object."""

        return {
            "choices": [{"message": {"content": '{"arguments":{"value":"fixed"}}'}}]
        }


class _Client:
    """Minimal async HTTP client test double."""

    async def __aenter__(self) -> "_Client":
        """Enter the fake client context."""

        return self

    async def __aexit__(self, *_args: object) -> None:
        """Leave the fake client context."""

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        """Return the deterministic model response."""

        return _Response()


@pytest.mark.asyncio
async def test_llm_advisor_parses_structured_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The advisor returns only the model's validated arguments object."""

    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _Client())
    advisor = LLMRepairAdvisor("http://repair.local", "repair-model", "secret", 1.0)
    repaired = await advisor.repair(
        "demo",
        {"value": "bad"},
        {"type": "object"},
        FailureDiagnosis(RepairFailure.TYPE_COERCION, "wrong type"),
    )
    assert repaired == {"value": "fixed"}


class _DenyRepairedPolicy:
    """Policy client that denies the repaired argument set."""

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Allow the first query and deny every repaired query."""

        if policy_input["arguments"]["query"] == "SELECT order_id FROM orders":
            return PolicyDecision(
                outcome=DENY,
                matched_rule="repaired_denied",
                reason="Repaired arguments denied",
            )
        return PolicyDecision(
            outcome=ALLOW,
            matched_rule="initial_allow",
            reason="Initial call allowed",
        )


class _QueryRepairAdvisor:
    """Repair advisor used to prove that policy is checked after repair."""

    async def repair(
        self,
        _tool_name: str,
        _arguments: Mapping[str, Any],
        _tool_schema: Mapping[str, Any],
        _diagnosis: FailureDiagnosis,
    ) -> Mapping[str, Any]:
        """Return a query that the test policy rejects."""

        return {"query": "SELECT order_id FROM orders"}


@pytest.mark.asyncio
async def test_repaired_arguments_are_rechecked_by_policy(tmp_path: Path) -> None:
    """A repaired request cannot bypass a second policy evaluation."""

    database_path = tmp_path / "gateway.sqlite"
    await seed_database(database_path)
    settings = Settings(environment="test", database_path=database_path)
    result = await execute_tool_call(
        create_registry(settings),
        InMemoryStateStore(),
        create_tracing(),
        "session-1",
        types.CallToolRequestParams(name="db_query", arguments={}),
        _DenyRepairedPolicy(),
        repair_advisor=_QueryRepairAdvisor(),
    )
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "policy_denied"
