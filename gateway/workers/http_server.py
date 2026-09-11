"""HTTP service that owns execution workers and sandbox lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from gateway import __version__
from gateway.config import Settings, get_settings
from gateway.errors import GatewayError
from gateway.sandbox.runner import SandboxRunner
from gateway.server import create_registry
from gateway.tools.db_query import seed_database
from gateway.workers.models import WorkerJob, WorkerResult, utc_now
from gateway.workers.service import WorkerService


def _health_payload(status: str) -> JSONResponse:
    """Build a worker health response with a stable schema."""

    return JSONResponse(
        {"status": status, "version": __version__},
        status_code=200 if status == "ok" else 503,
    )


def create_worker_app(settings: Settings | None = None) -> FastAPI:
    """Create the authenticated execution-worker HTTP application."""

    active_settings = settings or get_settings()
    if active_settings.worker_shared_secret is None:
        raise ValueError("GATEWAY_WORKER_SHARED_SECRET is required for worker service")
    sandbox_runner = (
        None
        if active_settings.environment == "test"
        else SandboxRunner(
            active_settings.sandbox_runtime,
            active_settings.sandbox_image,
            active_settings.sandbox_output_limit_bytes,
        )
    )
    service = WorkerService(
        create_registry(active_settings, sandbox_runner),
        active_settings.worker_shared_secret.get_secret_value(),
        max_concurrency=active_settings.worker_max_concurrency,
        queue_size=active_settings.worker_queue_size,
        job_timeout_seconds=active_settings.worker_job_timeout_seconds,
        failure_threshold=active_settings.worker_failure_threshold,
        reset_timeout_seconds=active_settings.worker_reset_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Initialize the worker pool and release it during shutdown."""

        await seed_database(active_settings.database_path)
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="MCP Gateway Execution Worker", lifespan=lifespan)

    @app.get("/livez")
    async def livez() -> JSONResponse:
        """Report worker process liveness without dependency checks."""

        return _health_payload("ok")

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Report whether the bounded worker pool is accepting jobs."""

        return _health_payload("ok" if await service.healthcheck() else "unready")

    @app.get("/startupz")
    async def startupz() -> JSONResponse:
        """Report completion of worker application initialization."""

        return _health_payload("ok")

    @app.post("/jobs")
    async def submit_job(request: Request) -> JSONResponse:
        """Authenticate, execute, and return one worker job result."""

        try:
            job = WorkerJob.model_validate_json(await request.body())
        except ValidationError:
            return JSONResponse({"error": "invalid worker job"}, status_code=422)
        try:
            service.authenticate(job)
            result = await service.submit(job)
        except GatewayError as error:
            status_code = (
                401
                if error.response.error.message == "Worker authentication failed"
                else 200
            )
            result = WorkerResult(
                job_id=job.job_id,
                outcome="failed",
                error_code=error.response.error.code,
                error_message=error.response.error.message,
                completed_at=utc_now(),
            )
            return JSONResponse(result.model_dump(mode="json"), status_code=status_code)
        return JSONResponse(result.model_dump(mode="json"))

    app.state.worker_service = service
    return app


def main() -> None:
    """Run the standalone execution-worker HTTP service."""

    settings = get_settings()
    uvicorn.run(
        create_worker_app(settings),
        host=settings.worker_host,
        port=settings.worker_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
