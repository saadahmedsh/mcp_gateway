"""Tests for span hierarchy and tool-call span attributes."""

from pathlib import Path

import pytest
from mcp import types
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gateway.config import Settings
from gateway.server import create_registry, execute_tool_call
from gateway.state.redis_store import InMemoryStateStore
from gateway.tools.db_query import seed_database
from gateway.tracing.otel import configure_logging, create_tracing


@pytest.mark.asyncio
async def test_tool_call_creates_root_and_child_spans(tmp_path: Path) -> None:
    """Verify the five-span Phase 2 trace and its required attributes."""

    configure_logging("INFO")
    exporter = InMemorySpanExporter()
    tracing = create_tracing(exporter=exporter)
    settings = Settings(database_path=tmp_path / "gateway.sqlite")
    await seed_database(settings.database_path)
    store = InMemoryStateStore()
    registry = create_registry(settings)

    result = await execute_tool_call(
        registry,
        store,
        tracing,
        "session-1",
        types.CallToolRequestParams(
            name="db_query",
            arguments={"query": "SELECT order_id FROM orders"},
        ),
    )
    tracing.shutdown()

    assert result.is_error is False
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "tool_call",
        "validate",
        "policy",
        "sandbox",
        "execute",
    }
    root = next(span for span in spans if span.name == "tool_call")
    assert root.attributes is not None
    assert root.attributes["tool_name"] == "db_query"
    assert root.attributes["risk_class"] == "read_only"
    assert all(span.context.trace_id == root.context.trace_id for span in spans)
    assert all(span.parent is not None for span in spans if span is not root)

    call_ids = await store.list_session_calls("session-1")
    record = await store.get_call(call_ids[0])
    assert record is not None
    assert record.trace_id == f"{root.context.trace_id:032x}"
