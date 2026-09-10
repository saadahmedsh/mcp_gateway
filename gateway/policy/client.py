"""Async, fail-closed client for the OPA decision endpoint."""

from typing import Any, Protocol

import httpx

from gateway.errors import PolicyUnavailableError
from gateway.models import PolicyDecision
from gateway.policy.decisions import ALLOW, DENY, REQUIRES_APPROVAL


class PolicyClient(Protocol):
    """Interface for policy evaluation used by the request pipeline."""

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Evaluate one tool call."""


class OpaPolicyClient:
    """Evaluate policy through OPA's HTTP data API."""

    def __init__(self, opa_url: str, timeout_seconds: float = 2.0) -> None:
        """Create an OPA client with a bounded request timeout."""

        self._url = f"{opa_url.rstrip('/')}/v1/data/mcp_gateway/decision"
        self._timeout = timeout_seconds

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Return OPA's decision or fail closed when OPA is unreachable."""

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json={"input": policy_input})
                response.raise_for_status()
                result = response.json().get("result")
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise PolicyUnavailableError() from error
        if not isinstance(result, dict):
            raise PolicyUnavailableError()
        outcome = result.get("outcome")
        if outcome not in {ALLOW, REQUIRES_APPROVAL, DENY}:
            raise PolicyUnavailableError()
        return PolicyDecision(
            outcome=outcome,
            matched_rule=str(result.get("matched_rule", "unknown")),
            reason=str(result.get("reason", "")),
        )


class AllowAllPolicyClient:
    """Explicit test adapter that permits calls without external OPA."""

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Return an allow decision for isolated unit tests."""

        del policy_input
        return PolicyDecision(
            outcome=ALLOW,
            matched_rule="test_allow_all",
            reason="OPA bypass is enabled only for direct isolated tests",
        )
