"""Run seeded realistic benchmark scenarios through one live MCP session."""

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gateway.async_utils import run_blocking
from eval.scenario_generator import generate_scenarios
from gateway.config import Settings


def _percentile(values: list[float], percentile: float) -> float:
    """Return a linearly interpolated percentile for non-empty values."""

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """Write one benchmark report to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _category_breakdown(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarize observed correctness by scenario category."""

    breakdown: dict[str, dict[str, Any]] = {}
    for result in results:
        category = str(result["category"])
        entry = breakdown.setdefault(category, {"count": 0, "passed": 0, "failed": 0})
        entry["count"] += 1
        if result["passed"]:
            entry["passed"] += 1
        else:
            entry["failed"] += 1
    for entry in breakdown.values():
        entry["pass_rate"] = entry["passed"] / entry["count"]
    return breakdown


def _read_last_audit(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read repair evidence and attempt count from the latest audit record."""

    if not path.exists():
        return [], 1
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not lines:
        return [], 1
    payload = json.loads(lines[-1])
    value = payload.get("repair_attempts", [])
    attempts = payload.get("attempts", [])
    attempt_count = len(attempts) if isinstance(attempts, list) else 1
    return (value if isinstance(value, list) else []), attempt_count


async def run_realistic_evaluation(
    seed: int = 20260910,
    repetitions: int = 5,
    output_path: Path = Path("eval/realistic-results.json"),
) -> dict[str, Any]:
    """Execute generated scenarios against one live gateway process."""

    scenarios = generate_scenarios(seed, repetitions)
    configured = Settings()
    with tempfile.TemporaryDirectory(prefix="mcp-realistic-") as directory:
        audit_path = Path(directory) / "audit.jsonl"
        environment = os.environ.copy()
        environment.update(
            {
                "GATEWAY_ENVIRONMENT": "development",
                "GATEWAY_STATE_STORE_BACKEND": "redis",
                "GATEWAY_APPROVAL_TIMEOUT_SECONDS": "2",
                "GATEWAY_AUDIT_LOG_PATH": str(audit_path),
                "GATEWAY_REPAIR_ENABLED": str(configured.repair_enabled).lower(),
                "GATEWAY_REPAIR_LLM_PROVIDER": configured.repair_llm_provider,
                "GATEWAY_REPAIR_LLM_URL": str(configured.repair_llm_url),
                "GATEWAY_REPAIR_LLM_MODEL": configured.repair_llm_model,
                "GATEWAY_REPAIR_LLM_TIMEOUT_SECONDS": str(
                    configured.repair_llm_timeout_seconds
                ),
                "GATEWAY_REPAIR_LLM_ANTHROPIC_VERSION": (
                    configured.repair_llm_anthropic_version
                ),
                "GATEWAY_REPAIR_LLM_MAX_TOKENS": str(configured.repair_llm_max_tokens),
            }
        )
        if configured.repair_llm_api_key is not None:
            environment["GATEWAY_REPAIR_LLM_API_KEY"] = (
                configured.repair_llm_api_key.get_secret_value()
            )
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "gateway.server"],
            cwd=Path.cwd(),
            env=environment,
        )
        results: list[dict[str, Any]] = []
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for scenario in scenarios:
                    started = time.perf_counter()
                    result = await session.call_tool(
                        str(scenario["tool"]),
                        dict(scenario["arguments"]),
                        read_timeout_seconds=30,
                    )
                    observed = "succeeded" if not result.is_error else "blocked"
                    repair_attempts, attempt_count = _read_last_audit(audit_path)
                    results.append(
                        {
                            "name": scenario["name"],
                            "category": scenario["category"],
                            "arguments": scenario["arguments"],
                            "expected": scenario["expected"],
                            "observed": observed,
                            "passed": observed == scenario["expected"],
                            "latency_ms": (time.perf_counter() - started) * 1000,
                            "attempt_count": attempt_count,
                            "repair_attempts": repair_attempts,
                        }
                    )
    latencies = [float(item["latency_ms"]) for item in results]
    malformed = [item for item in results if item["category"] == "malformed"]
    destructive = [item for item in results if item["category"] == "destructive"]
    adversarial = [item for item in results if item["category"] == "adversarial"]
    successful_attempts = [
        int(item["attempt_count"])
        for item in results
        if item["observed"] == "succeeded"
    ]
    report = {
        "run": {
            "seed": seed,
            "repetitions": repetitions,
            "scenario_generation": "seeded_random_v1",
            "repair_enabled": configured.repair_enabled,
            "repair_provider": configured.repair_llm_provider,
            "repair_model": configured.repair_llm_model,
        },
        "metrics": {
            "scenario_count": len(results),
            "pass_rate": (
                sum(1 for item in results if item["passed"]) / len(results)
                if results
                else 1.0
            ),
            "malformed_repaired_count": sum(1 for item in malformed if item["passed"]),
            "malformed_scenario_count": len(malformed),
            "mean_attempts_to_success": (
                statistics.mean(successful_attempts) if successful_attempts else 0.0
            ),
            "policy_gate_accuracy_percent": (
                100.0 if all(item["passed"] for item in destructive) else 0.0
            ),
            "sandbox_escape_attempts_blocked": sum(
                1 for item in adversarial if item["passed"]
            ),
            "sandbox_escape_attempts_total": len(adversarial),
            "latency_mean_ms": statistics.mean(latencies) if latencies else 0.0,
            "latency_p50_ms": _percentile(latencies, 0.50) if latencies else 0.0,
            "latency_p95_ms": _percentile(latencies, 0.95) if latencies else 0.0,
            "category_breakdown": _category_breakdown(results),
        },
        "results": results,
    }
    await run_blocking(_write_report, output_path, report)
    return report


def main() -> None:
    """Run the generated live benchmark and print its metrics."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--output", type=Path, default=Path("eval/realistic-results.json")
    )
    args = parser.parse_args()
    report = asyncio.run(
        run_realistic_evaluation(args.seed, args.repetitions, args.output)
    )
    sys.stdout.write(json.dumps(report["metrics"], indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
