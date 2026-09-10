"""Minimal end-to-end MCP client for the Phase 1 demo."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict


class DemoReport(BaseModel):
    """Machine-readable summary emitted by the demo client."""

    model_config = ConfigDict(strict=True)

    tools: list[dict[str, Any]]
    db_query: Any
    shell_exec: Any


async def run_demo(database_path: Path | None = None) -> DemoReport:
    """Start the gateway, discover tools, and exercise both Phase 1 paths."""

    environment = {
        "GATEWAY_APPROVAL_TIMEOUT_SECONDS": os.environ.get(
            "GATEWAY_APPROVAL_TIMEOUT_SECONDS", "300"
        )
    }
    if database_path is not None:
        environment["GATEWAY_DATABASE_PATH"] = str(database_path)

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gateway.server"],
        cwd=Path.cwd(),
        env=environment,
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            database_result = await session.call_tool(
                "db_query",
                {
                    "query": (
                        "SELECT order_id, customer_name, status "
                        "FROM orders WHERE status = :status ORDER BY order_id"
                    ),
                    "parameters": {"status": "pending"},
                },
            )
            shell_result = await session.call_tool(
                "shell_exec", {"command": "printf phase-1"}
            )

    return DemoReport(
        tools=[
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "risk_class": (tool.meta or {}).get("gateway/riskClass"),
            }
            for tool in listed.tools
        ],
        db_query=database_result.structured_content,
        shell_exec=shell_result.structured_content,
    )


def main() -> None:
    """Run the client and emit JSON without contaminating server stdio."""

    report = asyncio.run(run_demo())
    sys.stdout.write(report.model_dump_json(indent=2))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
