"""MCP stdio server backed by the gateway tool registry."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from gateway import __version__
from gateway.config import Settings, get_settings
from gateway.errors import (
    ApprovalRejectedError,
    ApprovalTimeoutError,
    GatewayError,
    PolicyDeniedError,
    StateStoreUnavailableError,
    ToolExecutionError,
)
from gateway.hitl.queue import (
    ApprovalQueue,
    RedisApprovalQueue,
    create_approval_request,
)
from gateway.models import (
    AttemptRecord,
    RiskClass,
    StateTransition,
    ToolCallRecord,
    ToolCallState,
)
from gateway.policy.client import (
    AllowAllPolicyClient,
    OpaPolicyClient,
    PolicyClient,
)
from gateway.policy.decisions import ALLOW, REQUIRES_APPROVAL
from gateway.registry import ToolDefinition, ToolRegistry
from gateway.state.redis_store import (
    InMemoryStateStore,
    StateStore,
    create_state_store,
)
from gateway.tools.db_query import create_db_query_tool, seed_database
from gateway.tools.shell_exec import create_shell_exec_tool
from gateway.tracing.otel import TracingManager, configure_logging, create_tracing


def create_registry(settings: Settings) -> ToolRegistry:
    """Build the gateway's deterministic Phase 1 tool registry."""

    registry = ToolRegistry()
    registry.register(create_db_query_tool(settings.database_path))
    registry.register(create_shell_exec_tool())
    return registry


def _annotations_for(
    definition: ToolDefinition[Any, Any],
) -> types.ToolAnnotations:
    """Translate a gateway risk class into standard MCP tool annotations."""

    if definition.risk_class is RiskClass.READ_ONLY:
        return types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    return types.ToolAnnotations(
        read_only_hint=False,
        destructive_hint=definition.risk_class is RiskClass.DESTRUCTIVE,
        idempotent_hint=False,
        open_world_hint=False,
    )


def _mcp_tool(definition: ToolDefinition[Any, Any]) -> types.Tool:
    """Convert one registry definition into its MCP discovery representation."""

    return types.Tool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.input_schema,
        output_schema=definition.output_schema,
        annotations=_annotations_for(definition),
        _meta={"gateway/riskClass": definition.risk_class.value},
    )


def _tool_result(payload: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    """Create matching textual and structured MCP result content."""

    rendered = json.dumps(payload, sort_keys=True)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=rendered)],
        structured_content=payload,
        is_error=is_error,
    )


async def execute_tool_call(
    registry: ToolRegistry,
    state_store: StateStore,
    tracing: TracingManager,
    session_id: str,
    params: types.CallToolRequestParams,
    policy_client: PolicyClient | None = None,
    approval_queue: ApprovalQueue | None = None,
    approval_timeout_seconds: float = 300.0,
) -> types.CallToolResult:
    """Run one request through state persistence, tracing, and tool execution."""

    active_policy_client = policy_client or AllowAllPolicyClient()
    call_id = uuid4().hex
    tool_name = params.name
    definition: ToolDefinition[Any, Any] | None = None
    risk_class: RiskClass | None = None
    try:
        definition = registry.get(tool_name)
        risk_class = definition.risk_class
    except GatewayError:
        definition = None

    with tracing.span(
        "tool_call",
        {
            "tool_name": tool_name,
            "risk_class": risk_class.value if risk_class else "unknown",
            "attempt": 1,
        },
    ) as root_span:
        trace_id = f"{root_span.get_span_context().trace_id:032x}"
        now = datetime.now(UTC)
        record = ToolCallRecord(
            call_id=call_id,
            trace_id=trace_id,
            session_id=session_id,
            tool_name=tool_name,
            risk_class=risk_class,
            arguments=params.arguments or {},
            current_state=ToolCallState.RECEIVED,
            transitions=[StateTransition(state=ToolCallState.RECEIVED, timestamp=now)],
            attempts=[AttemptRecord(attempt=1, started_at=now)],
            created_at=now,
            updated_at=now,
        )
        try:
            await state_store.create_call(record)
        except StateStoreUnavailableError as error:
            return _tool_result(error.response.model_dump(mode="json"), is_error=True)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            session_id=session_id,
            tool_name=tool_name,
        )
        logger = structlog.get_logger()

        async def fail(error: GatewayError) -> types.CallToolResult:
            """Persist a failed state and return its structured client response."""

            try:
                terminal_state = (
                    ToolCallState.DENIED
                    if error.response.error.code
                    in {
                        "policy_denied",
                        "policy_unavailable",
                        "approval_timeout",
                        "approval_rejected",
                    }
                    else ToolCallState.FAILED
                )
                await state_store.transition(call_id, terminal_state, error=str(error))
                await state_store.finish_attempt(
                    call_id,
                    1,
                    "denied" if terminal_state is ToolCallState.DENIED else "failed",
                    error=str(error),
                )
            except StateStoreUnavailableError as state_error:
                return _tool_result(
                    state_error.response.model_dump(mode="json"), is_error=True
                )
            logger.info("tool_call_failed", error_code=error.response.error.code)
            return _tool_result(error.response.model_dump(mode="json"), is_error=True)

        try:
            with tracing.span(
                "validate",
                {"tool_name": tool_name, "attempt": 1},
            ):
                request = registry.validate(tool_name, params.arguments or {})
            await state_store.transition(call_id, ToolCallState.VALIDATED)

            with tracing.span(
                "policy",
                {"tool_name": tool_name, "attempt": 1, "decision": "not_evaluated"},
            ) as policy_span:
                active_definition = registry.get(tool_name)
                policy_input = {
                    "tool_name": tool_name,
                    "risk_class": active_definition.risk_class.value,
                    "arguments": request.model_dump(mode="json"),
                }
                decision = await active_policy_client.evaluate(policy_input)
                policy_span.set_attribute("decision", decision.outcome)
                policy_span.set_attribute("matched_rule", decision.matched_rule)
                await state_store.transition(call_id, ToolCallState.POLICY_CHECKED)

                if decision.outcome == REQUIRES_APPROVAL:
                    if approval_queue is None:
                        raise PolicyDeniedError(
                            "Approval is required but no approval queue is configured"
                        )
                    approval = create_approval_request(
                        call_id,
                        tool_name,
                        request.model_dump(mode="json"),
                        policy_input,
                        decision.matched_rule,
                    )
                    await state_store.transition(
                        call_id, ToolCallState.AWAITING_APPROVAL
                    )
                    await approval_queue.submit(approval)
                    try:
                        resolved = await approval_queue.wait(
                            approval.approval_id, approval_timeout_seconds
                        )
                    except (ApprovalTimeoutError, ApprovalRejectedError):
                        await state_store.transition(
                            call_id, ToolCallState.DENIED, error=decision.reason
                        )
                        await state_store.finish_attempt(
                            call_id, 1, "denied", error=decision.reason
                        )
                        raise
                    policy_span.set_attribute(
                        "approver", resolved.approver or "unknown"
                    )
                elif decision.outcome != ALLOW:
                    await state_store.transition(
                        call_id, ToolCallState.DENIED, error=decision.reason
                    )
                    await state_store.finish_attempt(
                        call_id, 1, "denied", error=decision.reason
                    )
                    raise PolicyDeniedError(decision.reason)

            with tracing.span(
                "sandbox",
                {"tool_name": tool_name, "attempt": 1, "runtime": "in_process"},
            ):
                await state_store.transition(call_id, ToolCallState.EXECUTING)

            with tracing.span(
                "execute",
                {"tool_name": tool_name, "attempt": 1},
            ):
                output = await registry.execute_validated(tool_name, request)
            await state_store.transition(call_id, ToolCallState.SUCCEEDED)
            await state_store.finish_attempt(call_id, 1, "succeeded")
            logger.info("tool_call_succeeded", attempt=1)
            return _tool_result(output.model_dump(mode="json"), is_error=False)
        except GatewayError as error:
            return await fail(error)
        except Exception:
            return await fail(ToolExecutionError("The tool failed unexpectedly"))


def create_mcp_server(
    registry: ToolRegistry,
    state_store: StateStore | None = None,
    tracing: TracingManager | None = None,
    session_id: str | None = None,
    policy_client: PolicyClient | None = None,
    approval_queue: ApprovalQueue | None = None,
    approval_timeout_seconds: float = 300.0,
) -> Server[dict[str, Any]]:
    """Create the official MCP protocol adapter around the gateway runtime."""

    active_state_store = state_store or InMemoryStateStore()
    active_tracing = tracing or create_tracing()
    active_session_id = session_id or uuid4().hex
    active_policy_client = policy_client or AllowAllPolicyClient()

    async def list_tools(
        _context: ServerRequestContext[dict[str, Any]],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        """Return every registered tool with its authoritative schemas."""

        return types.ListToolsResult(
            tools=[_mcp_tool(definition) for definition in registry.list_tools()]
        )

    async def call_tool(
        _context: ServerRequestContext[dict[str, Any]],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """Dispatch one MCP request through the typed gateway registry."""

        return await execute_tool_call(
            registry,
            active_state_store,
            active_tracing,
            active_session_id,
            params,
            active_policy_client,
            approval_queue,
            approval_timeout_seconds,
        )

    return Server(
        "mcp-enterprise-agent-gateway",
        version=__version__,
        description="Typed MCP gateway tool boundary",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def run_stdio_server(settings: Settings | None = None) -> None:
    """Seed local data and serve one MCP connection over stdio."""

    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)
    await seed_database(active_settings.database_path)
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
    approval_queue = RedisApprovalQueue(str(active_settings.redis_url))
    session_id = uuid4().hex
    registry = create_registry(active_settings)

    server = create_mcp_server(
        registry,
        state_store=state_store,
        tracing=tracing,
        session_id=session_id,
        policy_client=policy_client,
        approval_queue=approval_queue,
        approval_timeout_seconds=active_settings.approval_timeout_seconds,
    )
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await state_store.close()
        await approval_queue.close()
        tracing.force_flush()
        tracing.shutdown()


def main() -> None:
    """Run the Phase 1 stdio entry point."""

    asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
