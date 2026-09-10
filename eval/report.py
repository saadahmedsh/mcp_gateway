"""Render evaluation JSON as a reproducible Markdown metrics table."""

import json
from pathlib import Path
from typing import Any


def render_report(report: dict[str, Any]) -> str:
    """Return a concise Markdown representation of evaluation metrics."""

    metrics = report["metrics"]
    lines = [
        "## Phase 6 evaluation metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for name, value in metrics.items():
        lines.append(f"| {name} | {value} |")
    return "\n".join(lines) + "\n"


def write_report(
    results_path: Path = Path("eval/results.json"),
    benchmarks_path: Path = Path("docs/BENCHMARKS.md"),
) -> None:
    """Append the latest machine-generated metrics to the benchmark document."""

    report = json.loads(results_path.read_text(encoding="utf-8"))
    existing = (
        benchmarks_path.read_text(encoding="utf-8") if benchmarks_path.exists() else ""
    )
    marker = "## Phase 6 evaluation metrics"
    prefix = existing.split(marker, maxsplit=1)[0].rstrip()
    benchmarks_path.write_text(
        prefix + "\n\n" + render_report(report), encoding="utf-8"
    )


def main() -> None:
    """Render the default evaluation result file."""

    write_report()


if __name__ == "__main__":
    main()
