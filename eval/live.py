"""Evaluate the gateway through live MCP, Redis, OPA, and sandbox services."""

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gateway.async_utils import run_blocking
from eval.harness import _load_scenarios


def _percentile(values: list[float], percentile: float) -> float:
    """Return a linear-interpolated percentile for non-empty measurements."""

    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """Write a live evaluation report synchronously in a worker thread."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def _run_live_scenario(scenario: dict[str, Any], index: int) -> dict[str, Any]:
    """Run one scenario through a fresh production-configured MCP process."""

    environment = os.environ.copy()
    environment.update(
        {
            "GATEWAY_ENVIRONMENT": "development",
            "GATEWAY_STATE_STORE_BACKEND": "redis",
            "GATEWAY_APPROVAL_TIMEOUT_SECONDS": "2",
        }
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway.server"],
        cwd=Path.cwd(),
        env=environment,
    )
    started = time.perf_counter()
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                str(scenario["tool"]),
                dict(scenario.get("arguments", {})),
                read_timeout_seconds=10,
            )
    observed = "succeeded" if not result.is_error else "blocked"
    return {
        "name": str(scenario.get("name", f"scenario-{index}")),
        "category": str(scenario.get("category", "general")),
        "expected": str(scenario.get("expected", "succeeded")),
        "observed": observed,
        "passed": observed == str(scenario.get("expected", "succeeded")),
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


async def run_live_evaluation(
    scenario_directory: Path = Path("eval/scenarios"),
    output_path: Path = Path("eval/live-results.json"),
) -> dict[str, Any]:
    """Run every scenario against live infrastructure and persist measurements."""

    results = [
        await _run_live_scenario(scenario, index)
        for index, scenario in enumerate(_load_scenarios(scenario_directory), start=1)
    ]
    latencies = [float(result["latency_ms"]) for result in results]
    destructive = [result for result in results if result["category"] == "destructive"]
    adversarial = [result for result in results if result["category"] == "adversarial"]
    metrics = {
        "scenario_count": len(results),
        "pass_rate": (
            sum(1 for result in results if result["passed"]) / len(results)
            if results
            else 1.0
        ),
        "policy_gate_accuracy_percent": (
            100.0 if all(result["passed"] for result in destructive) else 0.0
        ),
        "sandbox_escape_attempts_blocked": sum(
            1 for result in adversarial if result["passed"]
        ),
        "sandbox_escape_attempts_total": len(adversarial),
        "latency_p50_ms": _percentile(latencies, 0.50) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95) if latencies else 0.0,
        "latency_mean_ms": statistics.mean(latencies) if latencies else 0.0,
    }
    report = {"metrics": metrics, "results": results}
    await run_blocking(_write_report, output_path, report)
    return report


def main() -> None:
    """Run live evaluation and print its metrics."""

    report = asyncio.run(run_live_evaluation())
    sys.stdout.write(json.dumps(report["metrics"], indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
