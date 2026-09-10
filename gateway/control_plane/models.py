"""SQLAlchemy models for durable gateway control-plane data."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for persisted records."""

    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base class for all control-plane tables."""


class TenantModel(Base):
    """Tenant owning principals, calls, and approvals."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class PrincipalModel(Base):
    """Verified identity associated with a tenant."""

    __tablename__ = "principals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject", name="uq_principal_tenant_subject"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(512))
    agent_id: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class ToolCallModel(Base):
    """Durable summary of one tool invocation."""

    __tablename__ = "tool_calls"

    call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"))
    subject: Mapped[str | None] = mapped_column(String(256))
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    risk_class: Mapped[str | None] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    current_state: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_decision: Mapped[str | None] = mapped_column(String(64))
    policy_rule: Mapped[str | None] = mapped_column(String(256))
    outcome: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class AttemptModel(Base):
    """Execution attempt associated with a tool call."""

    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("call_id", "attempt", name="uq_attempt_call_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(
        ForeignKey("tool_calls.call_id"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)


class ApprovalModel(Base):
    """Human approval request and its final decision."""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"))
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    matched_rule: Mapped[str] = mapped_column(String(256), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(64))
    approver: Mapped[str | None] = mapped_column(String(256))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKeyModel(Base):
    """Request key preventing duplicate control-plane operations."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class PolicyVersionModel(Base):
    """Version and digest of a policy bundle evaluated by the gateway."""

    __tablename__ = "policy_versions"

    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    bundle_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    bundle_uri: Mapped[str | None] = mapped_column(String(1024))
    signed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class ConfigurationModel(Base):
    """Versioned control-plane configuration value."""

    __tablename__ = "configurations"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
