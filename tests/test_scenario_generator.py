"""Tests for reproducible benchmark scenario generation."""

from eval.scenario_generator import generate_scenarios


def test_scenario_generation_is_reproducible() -> None:
    """The same seed produces the same scenario corpus."""

    assert generate_scenarios(42, 3) == generate_scenarios(42, 3)


def test_scenario_generation_contains_varied_categories_and_inputs() -> None:
    """Generated runs contain all safety categories and varied inputs."""

    scenarios = generate_scenarios(42, 10)
    categories = {str(item["category"]) for item in scenarios}
    assert categories == {"valid", "malformed", "destructive", "adversarial"}
    statuses = {
        item["arguments"]["parameters"][0]
        for item in scenarios
        if item["category"] == "valid"
    }
    assert len(statuses) > 1
