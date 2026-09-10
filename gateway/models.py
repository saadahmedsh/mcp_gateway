"""Shared gateway data models."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects undeclared fields and implicit coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RiskClass(StrEnum):
    """Declared operational risk associated with a tool."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


class ToolCallState(StrEnum):
    """Lifecycle states persisted for one tool call."""

    RECEIVED = "received"
    VALIDATED = "validated"
    POLICY_CHECKED = "policy_checked"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class StateTransition(StrictModel):
    """One timestamped lifecycle transition."""

    state: ToolCallState
    timestamp: datetime
    error: str | None = None


class AttemptRecord(StrictModel):
    """Execution attempt metadata retained with a tool call."""

    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    outcome: str | None = None
    error: str | None = None


class RepairFailure(StrEnum):
    """Failure classes understood by the repair policy."""

    SCHEMA_MISMATCH = "schema_mismatch"
    MALFORMED_JSON = "malformed_json"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    TYPE_COERCION = "type_coercion"
    TIMEOUT = "timeout"
    SANDBOX_CRASH = "sandbox_crash"
    POLICY_DENIED = "policy_denied"
    TOOL_ERROR = "tool_error"


class ToolCallRecord(StrictModel):
    """Durable state and attempt history for one tool invocation."""

    call_id: str
    trace_id: str
    session_id: str
    tool_name: str
    risk_class: RiskClass | None
    arguments: dict[str, Any]
    current_state: ToolCallState
    transitions: list[StateTransition]
    attempts: list[AttemptRecord]
    created_at: datetime
    updated_at: datetime


class PolicyDecision(StrictModel):
    """Deterministic policy result returned by OPA."""

    outcome: str
    matched_rule: str
    reason: str


class ApprovalRequest(StrictModel):
    """Pending human approval request stored by the gateway."""

    approval_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    policy_input: dict[str, Any]
    matched_rule: str
    created_at: datetime
    decision: str | None = None
    approver: str | None = None
    reason: str | None = None


class ErrorIssue(StrictModel):
    """One machine-readable validation issue."""

    location: list[str | int]
    error_type: str
    message: str


class ErrorDetail(StrictModel):
    """Stable error payload returned through MCP."""

    code: str
    message: str
    issues: list[ErrorIssue] = Field(default_factory=list)


class ErrorResponse(StrictModel):
    """Top-level error response for an unsuccessful tool call."""

    error: ErrorDetail
