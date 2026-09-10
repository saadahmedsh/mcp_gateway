"""Authenticated Streamable HTTP entrypoint for Kubernetes deployments."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server.lowlevel import Server
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway import __version__
from gateway.audit.log import AuditLogger
from gateway.auth import (
    OIDCAuthenticator,
    principal_from_static_token,
    reset_current_principal,
    set_current_principal,
)
from gateway.config import Settings, get_settings
from gateway.control_plane.repository import ControlPlaneRepository
from gateway.errors import AuthenticationError
from gateway.hitl.queue import RedisApprovalQueue
from gateway.policy.client import AllowAllPolicyClient, OpaPolicyClient, PolicyClient
from gateway.repair.advisor import (
    AnthropicRepairAdvisor,
    LLMRepairAdvisor,
    RepairAdvisor,
)
from gateway.sandbox.runner import SandboxRunner
from gateway.server import create_mcp_server, create_registry
from gateway.state.redis_store import create_state_store
from gateway.tools.db_query import seed_database
from gateway.tracing.otel import configure_logging, create_tracing


class AuthenticationMiddleware:
    """Authenticate HTTP requests with static or OIDC bearer credentials."""

    def __init__(
        self,
        app: ASGIApp,
        mode: str,
        token: str | None = None,
        oidc: OIDCAuthenticator | None = None,
    ) -> None:
        """Create middleware around an ASGI application."""

        self.app = app
        self.mode = mode
        self.token = token
        self.oidc = oidc

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Authenticate HTTP requests before dispatching them."""

        if (
            scope["type"] != "http"
            or scope.get("path")
            in {
                "/livez",
                "/readyz",
                "/startupz",
            }
            or self.mode == "none"
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        try:
            if self.mode == "static_token":
                if self.token is None or credential != self.token:
                    raise AuthenticationError("Bearer token is invalid")
                principal = principal_from_static_token()
            elif self.mode == "oidc":
                if self.oidc is None:
                    raise AuthenticationError("OIDC authentication is not configured")
                principal = await self.oidc.authenticate(credential)
            else:
                raise AuthenticationError("HTTP authentication mode is invalid")
        except AuthenticationError:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        token = set_current_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_principal(token)


BearerTokenMiddleware = AuthenticationMiddleware


def _health_response(status: str, checks: dict[str, bool] | None = None) -> Response:
    """Build a stable JSON health response."""

    payload: dict[str, Any] = {"status": status, "version": __version__}
    if checks is not None:
        payload["checks"] = checks
    code = 200 if status == "ok" else 503
    return JSONResponse(payload, status_code=code)


def create_http_app(settings: Settings | None = None) -> ASGIApp:
    """Create the authenticated Streamable HTTP gateway application."""

    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)
    state_store = create_state_store(
        active_settings.state_store_backend,
        str(active_settings.redis_url),
        active_settings.redis_operation_timeout_seconds,
        active_settings.state_ttl_seconds,
    )
    tracing = create_tracing(str(active_settings.otlp_endpoint))
    policy_client: PolicyClient = (
        AllowAllPolicyClient()
        if active_settings.environment == "test"
        else OpaPolicyClient(str(active_settings.opa_url))
    )
    approval_queue = RedisApprovalQueue(
        str(active_settings.redis_url),
        ttl_seconds=active_settings.state_ttl_seconds,
    )
    control_plane = (
        ControlPlaneRepository(
            str(active_settings.control_plane_database_url),
            pool_size=active_settings.control_plane_pool_size,
            max_overflow=active_settings.control_plane_max_overflow,
            connect_timeout_seconds=active_settings.control_plane_connect_timeout_seconds,
        )
        if active_settings.control_plane_enabled
        else None
    )
    audit_logger = AuditLogger(active_settings.audit_log_path)
    repair_advisor: RepairAdvisor | None = None
    if (
        active_settings.repair_enabled
        and active_settings.repair_llm_api_key is not None
    ):
        advisor_arguments = (
            str(active_settings.repair_llm_url),
            active_settings.repair_llm_model,
            active_settings.repair_llm_api_key.get_secret_value(),
            active_settings.repair_llm_timeout_seconds,
        )
        if active_settings.repair_llm_provider == "anthropic":
            repair_advisor = AnthropicRepairAdvisor(
                *advisor_arguments,
                anthropic_version=active_settings.repair_llm_anthropic_version,
                max_tokens=active_settings.repair_llm_max_tokens,
            )
        else:
            repair_advisor = LLMRepairAdvisor(*advisor_arguments)
    sandbox_runner = (
        None
        if active_settings.environment == "test"
        else SandboxRunner(
            active_settings.sandbox_runtime,
            active_settings.sandbox_image,
            active_settings.sandbox_output_limit_bytes,
        )
    )
    registry = create_registry(active_settings, sandbox_runner)
    server: Server[dict[str, Any]] = create_mcp_server(
        registry,
        state_store=state_store,
        tracing=tracing,
        policy_client=policy_client,
        approval_queue=approval_queue,
        approval_timeout_seconds=active_settings.approval_timeout_seconds,
        audit_logger=audit_logger,
        sandbox_runtime=active_settings.sandbox_runtime,
        repair_advisor=repair_advisor,
        control_plane=control_plane,
    )

    async def livez(_request: Any) -> Response:
        """Report process liveness without checking dependencies."""

        return _health_response("ok")

    async def startupz(_request: Any) -> Response:
        """Report that application initialization completed."""

        return _health_response("ok")

    async def readyz(_request: Any) -> Response:
        """Check state and policy dependencies before accepting traffic."""

        state_healthcheck = getattr(state_store, "healthcheck", None)
        policy_healthcheck = getattr(policy_client, "healthcheck", None)
        control_plane_healthcheck = (
            getattr(control_plane, "healthcheck", None)
            if control_plane is not None
            else None
        )
        checks = {
            "state_store": (
                bool(await state_healthcheck()) if callable(state_healthcheck) else True
            ),
            "policy": (
                bool(await policy_healthcheck())
                if callable(policy_healthcheck)
                else True
            ),
            "control_plane": (
                bool(await control_plane_healthcheck())
                if callable(control_plane_healthcheck)
                else True
            ),
        }
        return _health_response("ok" if all(checks.values()) else "unready", checks)

    mcp_app = server.streamable_http_app(
        streamable_http_path=active_settings.http_path,
        host=active_settings.http_host,
        max_request_body_size=active_settings.http_max_request_body_bytes,
        session_idle_timeout=active_settings.http_session_idle_timeout_seconds,
        max_sessions=active_settings.http_max_sessions,
    )
    mcp_app.routes.insert(0, Route("/livez", livez, methods=["GET"]))
    mcp_app.routes.insert(1, Route("/readyz", readyz, methods=["GET"]))
    mcp_app.routes.insert(2, Route("/startupz", startupz, methods=["GET"]))

    original_lifespan = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        """Initialize and close gateway dependencies with the HTTP app."""

        await seed_database(active_settings.database_path)
        try:
            async with original_lifespan(app):
                yield
        finally:
            await state_store.close()
            await approval_queue.close()
            if control_plane is not None:
                await control_plane.close()
            tracing.force_flush()
            tracing.shutdown()

    mcp_app.router.lifespan_context = lifespan
    token = (
        active_settings.http_auth_token.get_secret_value()
        if active_settings.http_auth_token is not None
        else None
    )
    oidc: OIDCAuthenticator | None = None
    if active_settings.http_auth_mode == "oidc":
        if (
            active_settings.oidc_issuer_url is None
            or active_settings.oidc_audience is None
            or active_settings.oidc_jwks_url is None
        ):
            raise ValueError(
                "OIDC mode requires issuer, audience, and JWKS configuration"
            )
        oidc = OIDCAuthenticator(
            str(active_settings.oidc_issuer_url),
            active_settings.oidc_audience,
            str(active_settings.oidc_jwks_url),
            active_settings.oidc_timeout_seconds,
        )
    return AuthenticationMiddleware(
        mcp_app,
        active_settings.http_auth_mode,
        token,
        oidc,
    )


def main() -> None:
    """Run the gateway Streamable HTTP server."""

    settings = get_settings()
    uvicorn.run(
        create_http_app(settings),
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
