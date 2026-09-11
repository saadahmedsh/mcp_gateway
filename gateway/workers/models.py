"""Typed job and result models exchanged with execution workers."""

from datetime import UTC, datetime
from typing import Any

from gateway.models import RiskClass, StrictModel


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


class WorkerJob(StrictModel):
    """Authenticated request submitted to an execution worker."""

    job_id: str
    call_id: str
    tenant_id: str
    tool_name: str
    risk_class: RiskClass
    arguments: dict[str, Any]
    idempotency_key: str
    submitted_at: datetime
    auth_token: str


class WorkerResult(StrictModel):
    """Bounded result returned by an execution worker."""

    job_id: str
    outcome: str
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    completed_at: datetime


class ReconciliationRecord(StrictModel):
    """Recovery state for a job whose execution outcome is uncertain."""

    job_id: str
    call_id: str
    idempotency_key: str
    risk_class: RiskClass
    status: str
    detail: str
    updated_at: datetime
