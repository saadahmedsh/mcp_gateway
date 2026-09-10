"""Deterministic exponential backoff with bounded jitter and wall time."""

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Retry limits and delay parameters."""

    max_attempts: int = 3
    base_seconds: float = 0.25
    max_seconds: float = 5.0
    max_total_seconds: float = 30.0
    jitter_ratio: float = 0.2

    def delay(self, retry_number: int, random: Random) -> float:
        """Return a jittered delay before the numbered retry."""

        raw = float(
            min(self.max_seconds, self.base_seconds * (2 ** (retry_number - 1)))
        )
        jitter = raw * self.jitter_ratio * float(random.uniform(-1.0, 1.0))
        return max(0.0, raw + jitter)
