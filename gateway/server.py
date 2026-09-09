"""MCP stdio server backed by the gateway tool registry."""

import asyncio
import json
from typing import Any

from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from gateway import __version__
from gateway.config import Settings, get_settings
from gateway.errors import GatewayError, ToolExecutionError
from gateway.models import RiskClass
from gateway.registry import ToolDefinition, ToolRegistry
from gateway.tools.db_query import create_db_query_tool, seed_database
from gateway.tools.shell_exec import create_shell_exec_tool


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


def create_mcp_server(registry: ToolRegistry) -> Server[dict[str, Any]]:
    """Create the official MCP protocol adapter around the tool registry."""

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

        try:
            output = await registry.execute(params.name, params.arguments or {})
        except GatewayError as error:
            return _tool_result(error.response.model_dump(mode="json"), is_error=True)
        except Exception:
            safe_error = ToolExecutionError("The tool failed unexpectedly")
            return _tool_result(
                safe_error.response.model_dump(mode="json"), is_error=True
            )
        return _tool_result(output.model_dump(mode="json"), is_error=False)

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
    await seed_database(active_settings.database_path)
    server = create_mcp_server(create_registry(active_settings))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Run the Phase 1 stdio entry point."""

    asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
