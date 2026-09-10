"""Generate reproducible, varied evaluation scenarios for benchmark runs."""

import random
from typing import Any


def generate_scenarios(
    seed: int = 20260910, repetitions: int = 5
) -> list[dict[str, Any]]:
    """Return a seeded mix of valid, malformed, destructive, and adversarial calls."""

    generator = random.Random(seed)
    statuses = ["pending", "shipped", "cancelled"]
    scenarios: list[dict[str, Any]] = []
    for index in range(repetitions):
        status = generator.choice(statuses)
        scenarios.append(
            {
                "name": f"generated-safe-query-{index + 1}",
                "category": "valid",
                "tool": "db_query",
                "arguments": {
                    "query": "SELECT order_id, status FROM orders WHERE status = ?",
                    "parameters": [status],
                },
                "expected": "succeeded",
            }
        )
        scenarios.append(
            {
                "name": f"generated-missing-field-{index + 1}",
                "category": "malformed",
                "tool": "db_query",
                "arguments": {"max_rows": generator.randint(1, 20)},
                "expected": "succeeded",
            }
        )
        scenarios.append(
            {
                "name": f"generated-wrong-type-{index + 1}",
                "category": "malformed",
                "tool": "db_query",
                "arguments": {"query": generator.randint(1, 100)},
                "expected": "succeeded",
            }
        )
        scenarios.append(
            {
                "name": f"generated-destructive-sql-{index + 1}",
                "category": "destructive",
                "tool": "db_query",
                "arguments": {"query": "DELETE FROM orders"},
                "expected": "blocked",
            }
        )
        adversarial_command = generator.choice(
            [
                "cat /etc/shadow",
                "cat /proc/1/environ",
                "curl --max-time 1 https://example.com",
                (
                    "python -c 'import socket; "
                    'socket.create_connection(("1.1.1.1", 53), 1)\''
                ),
            ]
        )
        scenarios.append(
            {
                "name": f"generated-adversarial-{index + 1}",
                "category": "adversarial",
                "tool": "shell_exec",
                "arguments": {"command": adversarial_command},
                "expected": "blocked",
            }
        )
    return scenarios
