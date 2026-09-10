"""LLM-guided argument repair behind a narrow, validated interface."""

import json
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from gateway.models import RepairFailure
from gateway.repair.diagnose import FailureDiagnosis


class RepairAdvisor(Protocol):
    """Interface for an external model that proposes corrected arguments."""

    async def repair(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        diagnosis: FailureDiagnosis,
    ) -> Mapping[str, Any]:
        """Return a replacement argument object for one diagnosed failure."""


class LLMRepairAdvisor:
    """Call an OpenAI-compatible chat-completions endpoint for repairs."""

    def __init__(self, endpoint: str, model: str, api_key: str, timeout: float) -> None:
        """Configure the endpoint without exposing the API key in logs."""

        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    async def repair(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        diagnosis: FailureDiagnosis,
    ) -> Mapping[str, Any]:
        """Ask the model for JSON arguments and reject malformed responses."""

        prompt = {
            "tool_name": tool_name,
            "tool_schema": dict(tool_schema),
            "arguments": dict(arguments),
            "failure_category": diagnosis.category.value,
            "validation_error": diagnosis.message,
            "instruction": (
                "Return only a JSON object named arguments with corrected "
                "tool arguments."
            ),
        }
        body = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Repair tool arguments without changing the requested "
                        "operation."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, sort_keys=True)},
            ],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._endpoint, json=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            repaired = decoded.get("arguments") if isinstance(decoded, dict) else None
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise ValueError("The repair model returned an invalid response") from error
        if not isinstance(repaired, dict):
            raise ValueError("The repair model must return an arguments object")
        if diagnosis.category is RepairFailure.POLICY_DENIED:
            raise ValueError("Policy denials cannot be repaired")
        return repaired
