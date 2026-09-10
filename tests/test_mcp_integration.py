"""End-to-end MCP client/server tests over the real stdio transport."""

import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from gateway.tools.db_query import DbQueryInput
from gateway.tools.shell_exec import ShellExecInput


def _render_text(result: types.CallToolResult) -> str:
    """Combine textual MCP content for safe error assertions."""

    return "\n".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    )


@pytest.mark.asyncio
async def test_mcp_tools_end_to_end(tmp_path: Path) -> None:
    """Verify discovery, execution, validation, and refusal over MCP stdio."""

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway.server"],
        cwd=Path.cwd(),
        env={
            "GATEWAY_DATABASE_PATH": str(tmp_path / "gateway.sqlite"),
            "GATEWAY_STATE_STORE_BACKEND": "memory",
            "GATEWAY_ENVIRONMENT": "test",
            "GATEWAY_REPAIR_ENABLED": "false",
        },
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {"db_query", "shell_exec"}
            assert tools["db_query"].input_schema == DbQueryInput.model_json_schema()
            assert (
                tools["shell_exec"].input_schema == ShellExecInput.model_json_schema()
            )

            valid = await session.call_tool(
                "db_query",
                {"query": "SELECT order_id FROM orders ORDER BY order_id"},
                read_timeout_seconds=5,
            )
            assert valid.is_error is False
            assert isinstance(valid.structured_content, dict)
            assert valid.structured_content["row_count"] == 3

            invalid = await session.call_tool(
                "db_query", {"max_rows": 10}, read_timeout_seconds=5
            )
            assert invalid.is_error is True
            assert isinstance(invalid.structured_content, dict)
            assert invalid.structured_content["error"]["code"] == "schema_validation"
            assert "Traceback" not in _render_text(invalid)

            disabled = await session.call_tool(
                "shell_exec", {"command": "whoami"}, read_timeout_seconds=5
            )
            assert disabled.is_error is True
            assert isinstance(disabled.structured_content, dict)
            assert disabled.structured_content["error"]["code"] == "tool_not_enabled"
            assert "not enabled until Phase 4" in _render_text(disabled)
