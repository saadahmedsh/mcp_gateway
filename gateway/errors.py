"""Typed gateway exception hierarchy."""

from collections.abc import Sequence

from pydantic import ValidationError

from gateway.models import ErrorDetail, ErrorIssue, ErrorResponse


class GatewayError(Exception):
    """Base class for anticipated errors safe to return to an MCP client."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        issues: Sequence[ErrorIssue] = (),
    ) -> None:
        """Initialize an anticipated error with its client-safe response."""

        super().__init__(message)
        self.response = ErrorResponse(
            error=ErrorDetail(code=code, message=message, issues=list(issues))
        )


class ToolNotFoundError(GatewayError):
    """Raised when a requested tool is not registered."""

    def __init__(self, tool_name: str) -> None:
        """Initialize an error for an unknown tool name."""

        super().__init__("tool_not_found", f"Unknown tool: {tool_name}")


class ToolInputError(GatewayError):
    """Raised when tool arguments fail Pydantic validation."""

    @classmethod
    def from_validation_error(cls, error: ValidationError) -> "ToolInputError":
        """Build a stable, JSON-safe error from a Pydantic validation error."""

        issues = [
            ErrorIssue(
                location=list(item["loc"]),
                error_type=item["type"],
                message=item["msg"],
            )
            for item in error.errors(include_url=False, include_context=False)
        ]
        return cls(
            "schema_validation",
            "Tool arguments did not match the required schema",
            issues=issues,
        )


class ToolNotEnabledError(GatewayError):
    """Raised when a registered tool is intentionally unavailable."""

    def __init__(self, message: str) -> None:
        """Initialize an error for an intentionally unavailable tool."""

        super().__init__("tool_not_enabled", message)


class ToolExecutionError(GatewayError):
    """Raised for an anticipated failure while executing a tool."""

    def __init__(self, message: str) -> None:
        """Initialize an anticipated tool execution error."""

        super().__init__("tool_execution_failed", message)


class StateStoreUnavailableError(GatewayError):
    """Raised when durable call state cannot be read or written."""

    def __init__(self) -> None:
        """Initialize a safe degraded-state response."""

        super().__init__(
            "state_store_unavailable",
            "The gateway could not persist tool-call state",
        )


class PolicyDeniedError(GatewayError):
    """Raised when deterministic policy denies a tool call."""

    def __init__(self, reason: str) -> None:
        """Initialize a policy denial with its rule explanation."""

        super().__init__("policy_denied", reason)


class PolicyUnavailableError(GatewayError):
    """Raised when OPA cannot evaluate a policy request."""

    def __init__(self) -> None:
        """Initialize a fail-closed policy error."""

        super().__init__("policy_unavailable", "Policy evaluation is unavailable")


class ApprovalTimeoutError(GatewayError):
    """Raised when an approval request expires without a decision."""

    def __init__(self) -> None:
        """Initialize an approval timeout denial."""

        super().__init__("approval_timeout", "Approval timed out; the call was denied")


class ApprovalRejectedError(GatewayError):
    """Raised when an operator rejects a pending call."""

    def __init__(self, reason: str) -> None:
        """Initialize an operator rejection."""

        super().__init__("approval_rejected", reason)
