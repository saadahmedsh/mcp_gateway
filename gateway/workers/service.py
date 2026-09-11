"""Bounded authenticated worker queue for tool execution."""

import asyncio
import hashlib
import hmac
import json
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from gateway.errors import GatewayError, NeedsReviewError, ToolExecutionError
from gateway.models import RiskClass
from gateway.registry import ToolRegistry
from gateway.workers.circuit import CircuitBreaker, CircuitOpenError
from gateway.workers.models import ReconciliationRecord, WorkerJob, WorkerResult


class WorkerClient(Protocol):
    """Interface used by gateway-side tool proxies."""

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk_class: RiskClass,
        call_id: str,
        tenant_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit a tool job and return its output mapping."""


_worker_context: ContextVar[tuple[str, str] | None] = ContextVar(
    "gateway_worker_context", default=None
)


def set_worker_context(call_id: str, tenant_id: str) -> Token[tuple[str, str] | None]:
    """Bind gateway call identity to the current execution task."""

    return _worker_context.set((call_id, tenant_id))


def reset_worker_context(token: Token[tuple[str, str] | None]) -> None:
    """Restore the previous worker execution context."""

    _worker_context.reset(token)


def _canonical_job_payload(job: WorkerJob) -> bytes:
    """Serialize job fields covered by the authentication signature."""

    values = job.model_dump(mode="json", exclude={"auth_token"})
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def _sign_job(job: WorkerJob, secret: str) -> str:
    """Create an HMAC-SHA256 authentication token for a job."""

    return hmac.new(
        secret.encode(), _canonical_job_payload(job), hashlib.sha256
    ).hexdigest()


def _idempotency_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Build a stable key for proxy calls without an explicit client key."""

    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    return f"{tool_name}:{hashlib.sha256(encoded).hexdigest()}"


class WorkerService:
    """Run authenticated jobs with bounded concurrency and recovery tracking."""

    def __init__(
        self,
        registry: ToolRegistry,
        shared_secret: str,
        max_concurrency: int = 4,
        queue_size: int = 128,
        job_timeout_seconds: float = 120.0,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 30.0,
    ) -> None:
        """Configure the worker registry, queue, limits, and circuit breaker."""

        self._registry = registry
        self._shared_secret = shared_secret
        self._queue: asyncio.Queue[tuple[WorkerJob, asyncio.Future[WorkerResult]]] = (
            asyncio.Queue(maxsize=queue_size)
        )
        self._max_concurrency = max(1, max_concurrency)
        self._job_timeout = job_timeout_seconds
        self._tasks: list[asyncio.Task[None]] = []
        self._idempotent_results: dict[str, WorkerResult] = {}
        self._inflight: dict[str, tuple[WorkerJob, asyncio.Future[WorkerResult]]] = {}
        self._reconciliation: dict[str, ReconciliationRecord] = {}
        self._lock = asyncio.Lock()
        self._breaker = CircuitBreaker(failure_threshold, reset_timeout_seconds)

    @property
    def reconciliation(self) -> dict[str, ReconciliationRecord]:
        """Return a snapshot of jobs requiring recovery review."""

        return dict(self._reconciliation)

    async def healthcheck(self) -> bool:
        """Report whether the worker pool is started and accepting jobs."""

        return bool(self._tasks) and not self._breaker.is_open

    async def start(self) -> None:
        """Start bounded worker tasks."""

        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._worker_loop(), name=f"gateway-worker-{index}")
            for index in range(self._max_concurrency)
        ]

    async def stop(self) -> None:
        """Stop workers and mark unfinished jobs for reconciliation."""

        async with self._lock:
            unfinished = list(self._inflight_jobs()) + self._queued_jobs()
            for job, future in unfinished:
                if not future.done():
                    if job.risk_class is not RiskClass.READ_ONLY:
                        self._mark_uncertain(
                            job, "Worker service stopped before completion"
                        )
                        future.set_exception(
                            NeedsReviewError(
                                "Worker stopped; mutation outcome requires review"
                            )
                        )
                    else:
                        future.set_exception(
                            ToolExecutionError(
                                "Worker stopped before read-only job completion"
                            )
                        )
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _queued_jobs(self) -> list[tuple[WorkerJob, asyncio.Future[WorkerResult]]]:
        """Drain queued jobs for deterministic shutdown handling."""

        values: list[tuple[WorkerJob, asyncio.Future[WorkerResult]]] = []
        while True:
            try:
                values.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return values

    def _inflight_jobs(self) -> list[tuple[WorkerJob, asyncio.Future[WorkerResult]]]:
        """Return jobs currently awaiting worker completion."""

        return list(self._inflight.values())

    def _mark_uncertain(self, job: WorkerJob, detail: str) -> None:
        """Record an uncertain job outcome for reconciliation."""

        self._reconciliation[job.job_id] = ReconciliationRecord(
            job_id=job.job_id,
            call_id=job.call_id,
            idempotency_key=job.idempotency_key,
            risk_class=job.risk_class,
            status="needs_review",
            detail=detail,
            updated_at=datetime.now(UTC),
        )

    async def submit(self, job: WorkerJob) -> WorkerResult:
        """Authenticate, deduplicate, enqueue, and await one worker job."""

        if not self._tasks:
            raise ToolExecutionError("Execution worker service is not running")
        try:
            self._breaker.allow()
        except CircuitOpenError as error:
            raise ToolExecutionError(str(error)) from error
        self.authenticate(job)
        existing_future: asyncio.Future[WorkerResult] | None = None
        async with self._lock:
            completed = self._idempotent_results.get(job.idempotency_key)
            if completed is not None:
                return completed
            existing = self._inflight.get(job.idempotency_key)
            if existing is not None:
                existing_future = existing[1]
            else:
                future: asyncio.Future[WorkerResult] = (
                    asyncio.get_running_loop().create_future()
                )
                self._inflight[job.idempotency_key] = (job, future)
                try:
                    self._queue.put_nowait((job, future))
                except asyncio.QueueFull as error:
                    del self._inflight[job.idempotency_key]
                    self._breaker.record_failure()
                    raise ToolExecutionError(
                        "Execution worker queue is full"
                    ) from error
        if existing_future is not None:
            return await existing_future
        try:
            result = await asyncio.wait_for(future, timeout=self._job_timeout)
        except TimeoutError as error:
            if job.risk_class is not RiskClass.READ_ONLY:
                self._mark_uncertain(
                    job, "Worker job exceeded the gateway wait timeout"
                )
            self._breaker.record_failure()
            if job.risk_class is not RiskClass.READ_ONLY:
                raise NeedsReviewError(
                    "Mutation outcome is uncertain; reconciliation required"
                ) from error
            raise ToolExecutionError("Execution worker timed out") from error
        if result.outcome != "succeeded":
            self._breaker.record_failure()
            if result.error_code == "needs_review":
                raise NeedsReviewError(
                    result.error_message or "Worker outcome requires review"
                )
            raise ToolExecutionError(result.error_message or "Execution worker failed")
        self._breaker.record_success()
        return result

    def authenticate(self, job: WorkerJob) -> None:
        """Verify the job signature before it enters the worker queue."""

        expected = _sign_job(
            job.model_copy(update={"auth_token": ""}), self._shared_secret
        )
        if not hmac.compare_digest(expected, job.auth_token):
            raise ToolExecutionError("Worker authentication failed")

    async def _worker_loop(self) -> None:
        """Consume jobs and resolve their futures."""

        while True:
            job, future = await self._queue.get()
            try:
                result = await self._execute(job)
                if not future.done():
                    future.set_result(result)
            except Exception as error:
                if not future.done():
                    future.set_result(
                        WorkerResult(
                            job_id=job.job_id,
                            outcome="failed",
                            error_code=(
                                error.response.error.code
                                if isinstance(error, GatewayError)
                                else "worker_error"
                            ),
                            error_message=str(error),
                            completed_at=datetime.now(UTC),
                        )
                    )
            finally:
                async with self._lock:
                    self._inflight.pop(job.idempotency_key, None)
                self._queue.task_done()

    async def _execute(self, job: WorkerJob) -> WorkerResult:
        """Execute one authenticated job through the worker-owned registry."""

        try:
            request = self._registry.validate(job.tool_name, job.arguments)
            output = await self._registry.execute_validated(job.tool_name, request)
        except Exception as error:
            if job.risk_class is not RiskClass.READ_ONLY:
                self._mark_uncertain(job, str(error))
            raise
        result = WorkerResult(
            job_id=job.job_id,
            outcome="succeeded",
            output=output.model_dump(mode="json"),
            completed_at=datetime.now(UTC),
        )
        async with self._lock:
            self._idempotent_results[job.idempotency_key] = result
        return result

    async def reconcile(
        self, job_id: str, confirmed_outcome: str | None = None
    ) -> ReconciliationRecord:
        """Resolve or retain a job's uncertain mutation outcome."""

        record = self._reconciliation.get(job_id)
        if record is None:
            raise ToolExecutionError("No reconciliation record exists for this job")
        if confirmed_outcome is not None:
            record = record.model_copy(
                update={
                    "status": confirmed_outcome,
                    "detail": (
                        "Outcome confirmed by an operator or tool-specific reconciler"
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._reconciliation[job_id] = record
        return record


class InProcessWorkerClient:
    """Gateway-side client for the bounded worker service."""

    def __init__(self, service: WorkerService, shared_secret: str) -> None:
        """Create a signed client bound to one worker service."""

        self._service = service
        self._shared_secret = shared_secret

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk_class: RiskClass,
        call_id: str,
        tenant_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Sign and submit one job to the worker service."""

        job = WorkerJob(
            job_id=uuid4().hex,
            call_id=call_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            risk_class=risk_class,
            arguments=arguments,
            idempotency_key=idempotency_key or f"{call_id}:{tool_name}",
            submitted_at=datetime.now(UTC),
            auth_token="",
        )
        signed = job.model_copy(
            update={"auth_token": _sign_job(job, self._shared_secret)}
        )
        result = await self._service.submit(signed)
        if result.output is None:
            raise ToolExecutionError(
                result.error_message or "Execution worker returned no output"
            )
        return result.output


def create_worker_proxy_registry(
    source: ToolRegistry,
    client: WorkerClient,
) -> ToolRegistry:
    """Build a gateway registry whose handlers submit jobs to a worker client."""

    proxy = ToolRegistry()
    for definition in source.list_tools():

        async def handler(
            request: Any,
            definition_name: str = definition.name,
            definition_risk: RiskClass = definition.risk_class,
        ) -> Any:
            """Submit a validated request to the selected execution worker."""

            arguments = request.model_dump(mode="json")
            context = _worker_context.get()
            call_id = context[0] if context is not None else uuid4().hex
            tenant_id = context[1] if context is not None else "worker-proxy"
            return await client.execute(
                definition_name,
                arguments,
                definition_risk,
                call_id=call_id,
                tenant_id=tenant_id,
                idempotency_key=(
                    f"{call_id}:{definition_name}"
                    if context is not None
                    else _idempotency_key(definition_name, arguments)
                ),
            )

        proxy.register(
            type(definition)(
                name=definition.name,
                description=definition.description,
                risk_class=definition.risk_class,
                input_model=definition.input_model,
                output_model=definition.output_model,
                handler=handler,
                idempotent=definition.idempotent,
            )
        )
    return proxy
