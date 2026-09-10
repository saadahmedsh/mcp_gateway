"""Create durable control-plane tables."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_control_plane"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial control-plane schema."""

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "principals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=True),
        sa.Column("agent_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "subject", name="uq_principal_tenant_subject"),
    )
    op.create_table(
        "tool_calls",
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("subject", sa.String(length=256), nullable=True),
        sa.Column("tool_name", sa.String(length=256), nullable=False),
        sa.Column("risk_class", sa.String(length=64), nullable=True),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("current_state", sa.String(length=64), nullable=False),
        sa.Column("policy_decision", sa.String(length=64), nullable=True),
        sa.Column("policy_rule", sa.String(length=256), nullable=True),
        sa.Column("outcome", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("call_id"),
    )
    op.create_table(
        "attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["call_id"], ["tool_calls.call_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", "attempt", name="uq_attempt_call_number"),
    )
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(length=128), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("tool_name", sa.String(length=256), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("policy_input", sa.JSON(), nullable=False),
        sa.Column("matched_rule", sa.String(length=256), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=True),
        sa.Column("approver", sa.String(length=256), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("approval_id"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=256), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("key", "tenant_id"),
    )
    op.create_table(
        "policy_versions",
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("bundle_hash", sa.String(length=128), nullable=False),
        sa.Column("bundle_uri", sa.String(length=1024), nullable=True),
        sa.Column("signed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("version"),
    )
    op.create_table(
        "configurations",
        sa.Column("key", sa.String(length=256), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Drop the initial control-plane schema."""

    op.drop_table("configurations")
    op.drop_table("policy_versions")
    op.drop_table("idempotency_keys")
    op.drop_table("approvals")
    op.drop_table("attempts")
    op.drop_table("tool_calls")
    op.drop_table("principals")
    op.drop_table("tenants")
