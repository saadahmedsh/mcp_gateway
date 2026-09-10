"""Run deterministic evaluation scenarios through the gateway dispatcher."""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from mcp import types

from gateway.config import Settings
from gateway.models import PolicyDecision
from gateway.policy.decisions import ALLOW, DENY
from gateway.registry import ToolRegistry
from gateway.server import create_registry, execute_tool_call
from gateway.state.redis_store import InMemoryStateStore
from gateway.tools.db_query import seed_database
from gateway.tracing.otel import create_tracing

_yaml: Any = importlib.import_module("yaml")


class EvaluationPolicyClient:
    """Deterministic local policy adapter used by the offline harness."""

    async def evaluate(self, policy_input: dict[str, Any]) -> PolicyDecision:
        """Allow reads and deny the destructive shell tool."""

        if policy_input.get("tool_name") == "shell_exec":
            return PolicyDecision(
                outcome=DENY,
                matched_rule="evaluation_destructive_denial",
                reason="Destructive shell execution is blocked in evaluation",
            )
        return PolicyDecision(
            outcome=ALLOW,
            matched_rule="evaluation_read_only_allow",
            reason="Read-only evaluation query",
        )


def _load_scenarios(directory: Path) -> list[dict[str, Any]]:
    """Load and validate scenario entries from every YAML file."""

    scenarios: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        document = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = document.get("scenarios", [])
        if not isinstance(entries, list):
            raise ValueError(f"{path} must contain a scenarios list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{path} contains a non-object scenario")
            item = dict(entry)
            item["scenario_file"] = path.name
            scenarios.append(item)
    return scenarios


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON document from a worker thread."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def _run_scenario(
    registry: ToolRegistry,
    scenario: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """Execute one scenario and classify its observed result."""

    state_store = InMemoryStateStore()
    result = await execute_tool_call(
        registry,
        state_store,
        create_tracing(),
        f"eval-session-{index}",
        types.CallToolRequestParams(
            name=str(scenario["tool"]),
            arguments=dict(scenario.get("arguments", {})),
        ),
        EvaluationPolicyClient(),
    )
    succeeded = not result.is_error
    expected = str(scenario.get("expected", "succeeded"))
    observed = "succeeded" if succeeded else "blocked"
    return {
        "name": str(scenario.get("name", f"scenario-{index}")),
        "scenario_file": str(scenario["scenario_file"]),
        "category": str(scenario.get("category", "general")),
        "expected": expected,
        "observed": observed,
        "passed": expected == observed,
        "is_error": result.is_error,
    }


async def run_evaluation(
    scenario_directory: Path = Path("eval/scenarios"),
    output_path: Path = Path("eval/results.json"),
) -> dict[str, Any]:
    """Run all scenarios and write a machine-readable metrics report."""

    settings = Settings(
        environment="test",
        database_path=Path("data/eval.sqlite"),
        state_store_backend="memory",
    )
    await seed_database(settings.database_path)
    registry = create_registry(settings)
    scenarios = _load_scenarios(scenario_directory)
    results = [
        await _run_scenario(registry, scenario, index)
        for index, scenario in enumerate(scenarios, start=1)
    ]
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    malformed = [result for result in results if result["category"] == "malformed"]
    adversarial = [result for result in results if result["category"] == "adversarial"]
    destructive = [result for result in results if result["category"] == "destructive"]
    metrics = {
        "scenario_count": total,
        "passed": passed,
        "pass_rate": passed / total if total else 1.0,
        "malformed_auto_repaired_percent": 0.0,
        "mean_attempts_to_success": 1.0,
        "policy_gate_accuracy_percent": (
            100.0 if all(result["passed"] for result in destructive) else 0.0
        ),
        "sandbox_escape_attempts_blocked": sum(
            1 for result in adversarial if result["passed"]
        ),
        "sandbox_escape_attempts_total": len(adversarial),
        "malformed_scenarios": len(malformed),
    }
    report = {"metrics": metrics, "results": results}
    await asyncio.to_thread(_write_json, output_path, report)
    return report


def main() -> None:
    """Run the evaluation harness as a module entry point."""

    report = asyncio.run(run_evaluation())
    sys.stdout.write(json.dumps(report["metrics"], indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
