"""Async SQLAlchemy repository for durable gateway control-plane records."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from gateway.control_plane.models import (
    ApprovalModel,
    AttemptModel,
    Base,
    ConfigurationModel,
    IdempotencyKeyModel,
    PolicyVersionModel,
    PrincipalModel,
    TenantModel,
    ToolCallModel,
)
from gateway.models import ApprovalRequest, Principal, ToolCallRecord


class ControlPlaneRepository:
    """Persist durable control-plane data with explicit async transactions."""

    def __init__(
        self,
        database_url: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        connect_timeout_seconds: float = 3.0,
    ) -> None:
        """Create an async engine and session factory for the configured database."""

        connect_args: dict[str, Any] = {}
        if database_url.startswith("postgresql+asyncpg://"):
            connect_args["timeout"] = connect_timeout_seconds
        engine_options: dict[str, Any] = {
            "connect_args": connect_args,
            "pool_pre_ping": True,
        }
        if database_url.startswith("postgresql+"):
            engine_options.update(pool_size=pool_size, max_overflow=max_overflow)
        self.engine: AsyncEngine = create_async_engine(database_url, **engine_options)
        self._session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        """Create missing tables for tests and local bootstrap environments."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def healthcheck(self) -> bool:
        """Return whether the database accepts a lightweight connection."""

        try:
            async with self.engine.connect() as connection:
                await connection.exec_driver_sql("SELECT 1")
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Dispose the asynchronous engine and its connection pool."""

        await self.engine.dispose()

    async def upsert_tenant(self, tenant_id: str, name: str) -> None:
        """Create or update a tenant record."""

        async with self._session_factory.begin() as session:
            tenant = await session.get(TenantModel, tenant_id)
            if tenant is None:
                session.add(TenantModel(id=tenant_id, name=name))
            else:
                tenant.name = name

    async def upsert_principal(self, principal: Principal) -> None:
        """Create or update a verified principal within its tenant."""

        async with self._session_factory.begin() as session:
            query = select(PrincipalModel).where(
                PrincipalModel.tenant_id == principal.tenant_id,
                PrincipalModel.subject == principal.subject,
            )
            existing = (await session.execute(query)).scalar_one_or_none()
            values = {
                "tenant_id": principal.tenant_id,
                "subject": principal.subject,
                "roles": [role.value for role in principal.roles],
                "issuer": principal.issuer,
                "agent_id": principal.agent_id,
            }
            if existing is None:
                session.add(PrincipalModel(**values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)

    async def create_approval(
        self, request: ApprovalRequest, tenant_id: str | None = None
    ) -> None:
        """Persist a pending approval request."""

        async with self._session_factory.begin() as session:
            session.add(
                ApprovalModel(
                    approval_id=request.approval_id,
                    call_id=request.call_id,
                    tenant_id=tenant_id,
                    tool_name=request.tool_name,
                    arguments=request.arguments,
                    policy_input=request.policy_input,
                    matched_rule=request.matched_rule,
                    decision=request.decision,
                    approver=request.approver,
                    reason=request.reason,
                    created_at=request.created_at,
                )
            )

    async def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        approver: str,
        reason: str,
        decided_at: datetime,
    ) -> bool:
        """Resolve one pending approval and return whether it existed."""

        async with self._session_factory.begin() as session:
            approval = await session.get(ApprovalModel, approval_id)
            if approval is None or approval.decision is not None:
                return False
            approval.decision = decision
            approval.approver = approver
            approval.reason = reason
            approval.decided_at = decided_at
            return True

    async def upsert_tool_call(self, record: ToolCallRecord) -> None:
        """Persist a tool-call summary and replace its attempt history."""

        async with self._session_factory.begin() as session:
            existing = await session.get(ToolCallModel, record.call_id)
            principal = record.principal
            values = {
                "call_id": record.call_id,
                "trace_id": record.trace_id,
                "session_id": record.session_id,
                "tenant_id": principal.tenant_id if principal else None,
                "subject": principal.subject if principal else None,
                "tool_name": record.tool_name,
                "risk_class": record.risk_class.value if record.risk_class else None,
                "arguments": record.arguments,
                "current_state": record.current_state.value,
                "updated_at": record.updated_at,
            }
            if existing is None:
                session.add(ToolCallModel(created_at=record.created_at, **values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
            await session.flush()
            await session.execute(
                delete(AttemptModel).where(AttemptModel.call_id == record.call_id)
            )
            session.add_all(
                [
                    AttemptModel(
                        call_id=record.call_id,
                        attempt=attempt.attempt,
                        started_at=attempt.started_at,
                        finished_at=attempt.finished_at,
                        outcome=attempt.outcome,
                        error=attempt.error,
                    )
                    for attempt in record.attempts
                ]
            )

    async def put_idempotency_key(
        self, tenant_id: str, key: str, call_id: str, status: str
    ) -> None:
        """Insert or update a tenant-scoped idempotency key."""

        async with self._session_factory.begin() as session:
            existing = await session.get(IdempotencyKeyModel, (key, tenant_id))
            if existing is None:
                session.add(
                    IdempotencyKeyModel(
                        key=key,
                        tenant_id=tenant_id,
                        call_id=call_id,
                        status=status,
                    )
                )
            else:
                existing.call_id = call_id
                existing.status = status

    async def get_idempotency_key(
        self, tenant_id: str, key: str
    ) -> IdempotencyKeyModel | None:
        """Return one tenant-scoped idempotency record."""

        async with self._session_factory() as session:
            return await session.get(IdempotencyKeyModel, (key, tenant_id))

    async def record_policy_version(
        self, version: str, bundle_hash: str, bundle_uri: str | None, signed: bool
    ) -> None:
        """Persist a policy bundle version and digest."""

        async with self._session_factory.begin() as session:
            session.add(
                PolicyVersionModel(
                    version=version,
                    bundle_hash=bundle_hash,
                    bundle_uri=bundle_uri,
                    signed=signed,
                )
            )

    async def set_configuration(
        self, key: str, value: dict[str, Any], version: int = 1
    ) -> None:
        """Create or replace one versioned configuration value."""

        async with self._session_factory.begin() as session:
            existing = await session.get(ConfigurationModel, key)
            if existing is None:
                session.add(ConfigurationModel(key=key, value=value, version=version))
            else:
                existing.value = value
                existing.version = version

    async def list_attempts(self, call_id: str) -> Sequence[AttemptModel]:
        """Return attempts for a call ordered by attempt number."""

        async with self._session_factory() as session:
            result = await session.execute(
                select(AttemptModel)
                .where(AttemptModel.call_id == call_id)
                .order_by(AttemptModel.attempt)
            )
            return result.scalars().all()
