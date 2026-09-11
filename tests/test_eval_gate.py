"""Tests for deployment evaluation invariants."""

from eval.gate import validate_report


def test_gate_accepts_expected_security_blocks() -> None:
    """Allow reports where destructive and adversarial scenarios are blocked."""

    report = {
        "metrics": {"latency_p95_ms": 10},
        "results": [
            {
                "name": "safe",
                "category": "valid",
                "observed": "succeeded",
                "passed": True,
            },
            {
                "name": "delete",
                "category": "destructive",
                "observed": "blocked",
                "passed": True,
            },
            {
                "name": "escape",
                "category": "adversarial",
                "observed": "blocked",
                "passed": True,
            },
        ],
    }

    assert validate_report(report, max_p95_ms=20) == []


def test_gate_rejects_security_invariant_violation() -> None:
    """Reject a report that says an adversarial command executed."""

    report = {
        "metrics": {"latency_p95_ms": 10},
        "results": [
            {
                "name": "escape",
                "category": "adversarial",
                "observed": "succeeded",
                "passed": False,
            }
        ],
    }

    violations = validate_report(report, max_p95_ms=20)

    assert any("sandbox escape succeeded" in violation for violation in violations)


def test_gate_rejects_latency_regression() -> None:
    """Reject live results above the configured latency budget."""

    report = {
        "metrics": {"latency_p95_ms": 101},
        "results": [
            {
                "name": "safe",
                "category": "valid",
                "observed": "succeeded",
                "passed": True,
            }
        ],
    }

    assert any("latency p95" in violation for violation in validate_report(report, 100))
