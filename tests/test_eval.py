"""Tests for the scenario evaluation harness."""

import json
from pathlib import Path

import pytest

from eval.harness import run_evaluation
from eval.live import _scenario_passed


@pytest.mark.asyncio
async def test_evaluation_writes_machine_readable_results(tmp_path: Path) -> None:
    """The checked-in scenarios produce a passing, reproducible report."""

    output_path = tmp_path / "results.json"
    report = await run_evaluation(output_path=output_path)
    assert report["metrics"]["pass_rate"] == 1.0
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["metrics"] == report["metrics"]


def test_malformed_repair_is_a_passing_live_outcome() -> None:
    """Treat either safe rejection or successful repair as correct behavior."""

    scenario = {"category": "malformed", "expected": "blocked"}

    assert _scenario_passed(scenario, "blocked")
    assert _scenario_passed(scenario, "succeeded")
