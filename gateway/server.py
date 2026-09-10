"""MCP stdio server backed by the gateway tool registry."""

import asyncio
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from gateway import __version__
from gateway.audit.log import AuditLogger
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
    RepairAttemptRecord,
    RepairFailure,
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
from gateway.repair.advisor import (
    AnthropicRepairAdvisor,
    LLMRepairAdvisor,
    RepairAdvisor,
)
from gateway.repair.diagnose import FailureDiagnosis, diagnose
from gateway.repair.loop import RepairContext, RepairLoop
from gateway.sandbox.runner import SandboxRunner
from gateway.state.redis_store import (
    InMemoryStateStore,
    StateStore,
    create_state_store,
)
from gateway.tools.db_query import create_db_query_tool, seed_database
from gateway.tools.shell_exec import create_shell_exec_tool
from gateway.tracing.otel import TracingManager, configure_logging, create_tracing


def create_registry(
    settings: Settings,
    sandbox_runner: SandboxRunner | None = None,
) -> ToolRegistry:
    """Build the gateway's deterministic Phase 1 tool registry."""

    registry = ToolRegistry()
    registry.register(create_db_query_tool(settings.database_path, sandbox_runner))
    registry.register(create_shell_exec_tool(sandbox_runner))
    return registry


def _annotations_for(
    definition: ToolDefinition[Any, Any],
) -> types.ToolAnnotations:
    """Translate a gateway risk class into standard MCP tool annotations."""

    if definition.risk_class is RiskClass.READ_ONLY:
        return types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=definition.idempotent,
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
    audit_logger: AuditLogger | None = None,
    sandbox_runtime: str = "unknown",
    repair_advisor: RepairAdvisor | None = None,
) -> types.CallToolResult:
    """Run one request through state persistence, tracing, and tool execution."""

    active_policy_client = policy_client or AllowAllPolicyClient()
    call_id = uuid4().hex
    tool_name = params.name
    definition: ToolDefinition[Any, Any] | None = None
    risk_class: RiskClass | None = None
    started_monotonic = time.monotonic()
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
        latest_attempt = 1
        last_finished_attempt = 1
        policy_outcome = "not_evaluated"
        policy_input: dict[str, Any] = {}
        matched_rule: str | None = None
        approver_identity: str | None = None
        repair_attempts: list[RepairAttemptRecord] = []

        async def write_audit(outcome: str) -> None:
            """Write one terminal audit event without masking the client result."""

            if audit_logger is None:
                return
            attempts: list[AttemptRecord] = []
            try:
                current = await state_store.get_call(call_id)
                if current is not None:
                    attempts = current.attempts
            except StateStoreUnavailableError:
                logger.warning("audit_state_unavailable")
            try:
                await audit_logger.append(
                    call_id=call_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    tool_name=tool_name,
                    arguments=params.arguments or {},
                    policy_input=policy_input,
                    policy_decision=policy_outcome,
                    matched_rule=matched_rule,
                    approver_identity=approver_identity,
                    sandbox_runtime=sandbox_runtime,
                    attempts=attempts,
                    repair_attempts=repair_attempts,
                    outcome=outcome,
                    duration_ms=(time.monotonic() - started_monotonic) * 1000,
                )
            except Exception as audit_error:
                logger.warning("audit_write_failed", error=str(audit_error))

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
                    last_finished_attempt,
                    "denied" if terminal_state is ToolCallState.DENIED else "failed",
                    error=str(error),
                )
            except StateStoreUnavailableError as state_error:
                return _tool_result(
                    state_error.response.model_dump(mode="json"), is_error=True
                )
            await write_audit(
                "denied" if terminal_state is ToolCallState.DENIED else "failed"
            )
            logger.info("tool_call_failed", error_code=error.response.error.code)
            return _tool_result(error.response.model_dump(mode="json"), is_error=True)

        try:
            request_arguments = dict(params.arguments or {})
            with tracing.span(
                "validate",
                {"tool_name": tool_name, "attempt": 1},
            ):
                try:
                    request = registry.validate(tool_name, request_arguments)
                except GatewayError as validation_error:
                    diagnosis = diagnose(validation_error)
                    repairable = diagnosis.category in {
                        RepairFailure.SCHEMA_MISMATCH,
                        RepairFailure.MALFORMED_JSON,
                        RepairFailure.MISSING_REQUIRED_FIELD,
                        RepairFailure.TYPE_COERCION,
                    }
                    if repair_advisor is None or not repairable:
                        raise
                    original_arguments = dict(request_arguments)
                    proposed_arguments = dict(
                        await repair_advisor.repair(
                            tool_name,
                            request_arguments,
                            registry.get(tool_name).input_schema,
                            diagnosis,
                        )
                    )
                    event = RepairAttemptRecord(
                        category=diagnosis.category,
                        original_arguments=original_arguments,
                        proposed_arguments=proposed_arguments,
                        validation_passed=False,
                        outcome="proposed",
                    )
                    repair_attempts.append(event)
                    request_arguments = proposed_arguments
                    try:
                        request = registry.validate(tool_name, request_arguments)
                    except GatewayError:
                        event.outcome = "validation_failed"
                        raise
                    event.validation_passed = True
                    event.outcome = "validated"
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
                policy_outcome = decision.outcome
                matched_rule = decision.matched_rule
                if repair_attempts:
                    repair_attempts[-1].policy_outcome = decision.outcome
                    if decision.outcome != ALLOW:
                        repair_attempts[-1].outcome = "policy_denied"
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
                    approver_identity = resolved.approver
                elif decision.outcome != ALLOW:
                    await state_store.transition(
                        call_id, ToolCallState.DENIED, error=decision.reason
                    )
                    await state_store.finish_attempt(
                        call_id, 1, "denied", error=decision.reason
                    )
                    raise PolicyDeniedError(decision.reason)

            async def execute_attempt(arguments: Mapping[str, Any]) -> Any:
                """Execute one bounded attempt and persist its result."""

                nonlocal last_finished_attempt, latest_attempt
                attempt_number = latest_attempt
                if latest_attempt > 1:
                    await state_store.start_attempt(call_id, latest_attempt)
                await state_store.transition(call_id, ToolCallState.EXECUTING)
                try:
                    if latest_attempt > 1:
                        with tracing.span(
                            "policy",
                            {
                                "tool_name": tool_name,
                                "attempt": attempt_number,
                                "decision": "repaired_arguments",
                            },
                        ) as repaired_policy_span:
                            repaired_request = registry.validate(tool_name, arguments)
                            repaired_input = {
                                "tool_name": tool_name,
                                "risk_class": active_definition.risk_class.value,
                                "arguments": repaired_request.model_dump(mode="json"),
                            }
                            repaired_decision = await active_policy_client.evaluate(
                                repaired_input
                            )
                            if repair_attempts:
                                repair_attempts[-1].policy_outcome = (
                                    repaired_decision.outcome
                                )
                            repaired_policy_span.set_attribute(
                                "decision", repaired_decision.outcome
                            )
                            repaired_policy_span.set_attribute(
                                "matched_rule", repaired_decision.matched_rule
                            )
                            if repaired_decision.outcome != ALLOW:
                                if repair_attempts:
                                    repair_attempts[-1].outcome = "policy_denied"
                                raise PolicyDeniedError(
                                    "Repaired arguments were denied by policy"
                                )
                    with (
                        tracing.span(
                            "sandbox",
                            {
                                "tool_name": tool_name,
                                "attempt": attempt_number,
                                "runtime": "sandbox",
                            },
                        ),
                        tracing.span(
                            "execute",
                            {"tool_name": tool_name, "attempt": attempt_number},
                        ),
                    ):
                        validated = registry.validate(tool_name, arguments)
                        result = await registry.execute_validated(tool_name, validated)
                    await state_store.finish_attempt(
                        call_id, attempt_number, "succeeded"
                    )
                    return result
                except GatewayError as error:
                    await state_store.finish_attempt(
                        call_id, attempt_number, "failed", error=str(error)
                    )
                    raise
                finally:
                    last_finished_attempt = attempt_number
                    latest_attempt += 1

            async def repair_arguments(
                arguments: Mapping[str, Any], diagnosis: FailureDiagnosis
            ) -> Mapping[str, Any]:
                """Delegate malformed-argument repair to the configured model."""

                if repair_advisor is None:
                    raise ToolExecutionError(
                        "No repair advisor is configured for this tool call"
                    )
                proposed = dict(
                    await repair_advisor.repair(
                        tool_name,
                        arguments,
                        active_definition.input_schema,
                        diagnosis,
                    )
                )
                event = RepairAttemptRecord(
                    category=diagnosis.category,
                    original_arguments=dict(arguments),
                    proposed_arguments=proposed,
                    validation_passed=False,
                    outcome="proposed",
                )
                repair_attempts.append(event)
                try:
                    registry.validate(tool_name, proposed)
                except GatewayError:
                    event.outcome = "validation_failed"
                    raise
                event.validation_passed = True
                event.outcome = "validated"
                return proposed

            output, successful_attempt = await RepairLoop().run(
                request.model_dump(mode="json"),
                execute_attempt,
                RepairContext(
                    risk_class=active_definition.risk_class,
                    idempotent=active_definition.idempotent,
                    tool_name=tool_name,
                    tool_schema=active_definition.input_schema,
                ),
                repair_arguments if repair_advisor is not None else None,
            )
            await state_store.transition(call_id, ToolCallState.SUCCEEDED)
            await write_audit("succeeded")
            logger.info("tool_call_succeeded", attempt=successful_attempt)
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
    audit_logger: AuditLogger | None = None,
    sandbox_runtime: str = "unknown",
    repair_advisor: RepairAdvisor | None = None,
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
            audit_logger,
            sandbox_runtime,
            repair_advisor,
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
    approval_queue = RedisApprovalQueue(
        str(active_settings.redis_url),
        ttl_seconds=active_settings.state_ttl_seconds,
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
    session_id = uuid4().hex
    registry = create_registry(active_settings, sandbox_runner)

    server = create_mcp_server(
        registry,
        state_store=state_store,
        tracing=tracing,
        session_id=session_id,
        policy_client=policy_client,
        approval_queue=approval_queue,
        approval_timeout_seconds=active_settings.approval_timeout_seconds,
        audit_logger=audit_logger,
        sandbox_runtime=active_settings.sandbox_runtime,
        repair_advisor=repair_advisor,
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
