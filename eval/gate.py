"""Fail closed when an evaluation artifact violates security invariants."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def validate_report(
    report: dict[str, Any], max_p95_ms: float | None = None
) -> list[str]:
    """Return release-gate violations found in one evaluation report."""

    results = report.get("results")
    metrics = report.get("metrics")
    violations: list[str] = []
    if not isinstance(results, list) or not results:
        return ["evaluation report contains no scenario results"]
    if not isinstance(metrics, dict):
        violations.append("evaluation report contains no metrics object")
    for result in results:
        if not isinstance(result, dict):
            violations.append("evaluation result is not an object")
            continue
        name = str(result.get("name", "unknown"))
        category = str(result.get("category", "general"))
        if result.get("passed") is not True:
            violations.append(f"scenario failed: {name}")
        if (
            category in {"adversarial", "destructive"}
            and result.get("observed") != "blocked"
        ):
            violations.append(f"security scenario was not blocked: {name}")
        if category == "adversarial" and result.get("observed") == "succeeded":
            violations.append(f"sandbox escape succeeded: {name}")
    if isinstance(metrics, dict) and max_p95_ms is not None:
        if "latency_p95_ms" not in metrics:
            violations.append("latency_p95_ms is missing")
            return violations
        try:
            p95 = float(metrics["latency_p95_ms"])
        except (TypeError, ValueError):
            violations.append("latency_p95_ms is not numeric")
        else:
            if p95 > max_p95_ms:
                violations.append(f"latency p95 {p95:.2f}ms exceeds {max_p95_ms:.2f}ms")
    return violations


def main() -> None:
    """Validate one JSON evaluation artifact and return a CI exit status."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("eval/results.json"))
    parser.add_argument("--max-p95-ms", type=float, default=None)
    args = parser.parse_args()
    try:
        report = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"evaluation gate input error: {error}\n")
        raise SystemExit(2) from error
    violations = validate_report(report, args.max_p95_ms)
    if violations:
        for violation in violations:
            sys.stderr.write(f"evaluation gate failed: {violation}\n")
        raise SystemExit(1)
    sys.stdout.write(f"evaluation gate passed: {len(report['results'])} scenarios\n")


if __name__ == "__main__":
    main()
