"""Classify gateway failures into bounded repair strategies."""

from dataclasses import dataclass

from gateway.errors import GatewayError, SandboxError, SandboxTimeoutError
from gateway.models import RepairFailure


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    """Failure classification and human-readable repair context."""

    category: RepairFailure
    message: str


def diagnose(error: BaseException) -> FailureDiagnosis:
    """Classify one exception without rewriting its security meaning."""

    if isinstance(error, GatewayError):
        code = error.response.error.code
        if code in {
            "policy_denied",
            "policy_unavailable",
            "approval_timeout",
            "approval_rejected",
        }:
            return FailureDiagnosis(RepairFailure.POLICY_DENIED, str(error))
        if code == "schema_validation":
            issue_types = {issue.error_type for issue in error.response.error.issues}
            if any("missing" in issue_type for issue_type in issue_types):
                category = RepairFailure.MISSING_REQUIRED_FIELD
            elif any("json" in issue_type for issue_type in issue_types):
                category = RepairFailure.MALFORMED_JSON
            elif any("type" in issue_type for issue_type in issue_types):
                category = RepairFailure.TYPE_COERCION
            else:
                category = RepairFailure.SCHEMA_MISMATCH
            return FailureDiagnosis(category, str(error))
        if isinstance(error, SandboxTimeoutError):
            return FailureDiagnosis(RepairFailure.TIMEOUT, str(error))
        if isinstance(error, SandboxError):
            return FailureDiagnosis(RepairFailure.SANDBOX_CRASH, str(error))
        return FailureDiagnosis(RepairFailure.TOOL_ERROR, str(error))
    return FailureDiagnosis(RepairFailure.TOOL_ERROR, str(error))
