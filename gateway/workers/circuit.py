"""Circuit breaker protecting the gateway from an unhealthy worker pool."""

from datetime import UTC, datetime, timedelta


class CircuitOpenError(Exception):
    """Raised when worker submissions are temporarily suspended."""


class CircuitBreaker:
    """Trip after repeated worker failures and recover after a cool-down."""

    def __init__(
        self, failure_threshold: int = 3, reset_timeout_seconds: float = 30.0
    ) -> None:
        """Configure failure threshold and reset cool-down."""

        self._failure_threshold = max(1, failure_threshold)
        self._reset_timeout = reset_timeout_seconds
        self._failures = 0
        self._opened_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Return whether submissions are currently blocked."""

        if self._opened_at is None:
            return False
        elapsed = datetime.now(UTC) - self._opened_at
        return elapsed < timedelta(seconds=self._reset_timeout)

    def allow(self) -> None:
        """Permit a submission or raise while the circuit is open."""

        if self.is_open:
            raise CircuitOpenError("Execution worker circuit is open")
        if self._opened_at is not None:
            self._opened_at = None
            self._failures = 0

    def record_success(self) -> None:
        """Reset the breaker after a successful worker result."""

        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """Record a failure and open the circuit at the threshold."""

        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = datetime.now(UTC)
