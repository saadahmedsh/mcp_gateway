"""Tests for the tamper-evident audit log."""

import json
from pathlib import Path

import pytest
from mcp import types

from gateway.audit.log import AuditLogger
from gateway.audit.verify import verify_audit_file
from gateway.config import Settings
from gateway.errors import AuditIntegrityError
from gateway.server import create_registry, execute_tool_call
from gateway.state.redis_store import InMemoryStateStore
from gateway.tools.db_query import seed_database
from gateway.tracing.otel import create_tracing


@pytest.mark.asyncio
async def test_audit_records_form_a_valid_chain(tmp_path: Path) -> None:
    """Appended records verify and link to the preceding record."""

    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    await logger.append(
        call_id="call-1",
        session_id="session-1",
        trace_id="trace-1",
        tool_name="db_query",
        arguments={"query": "SELECT 1"},
        policy_decision="allow",
        matched_rule="read_only",
        approver_identity=None,
        sandbox_runtime="test",
        attempts=[],
        outcome="succeeded",
        duration_ms=1.0,
    )
    await logger.append(
        call_id="call-2",
        session_id="session-1",
        trace_id="trace-2",
        tool_name="db_query",
        arguments={"query": "SELECT 2"},
        policy_decision="allow",
        matched_rule="read_only",
        approver_identity=None,
        sandbox_runtime="test",
        attempts=[],
        outcome="succeeded",
        duration_ms=2.0,
    )
    assert verify_audit_file(path) is True


def test_audit_tampering_is_detected(tmp_path: Path) -> None:
    """Changing an event after writing invalidates the chain."""

    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "call_id": "call-1",
                "session_id": "session-1",
                "trace_id": "trace-1",
                "tool_name": "db_query",
                "arguments": {},
                "policy_decision": "allow",
                "matched_rule": "read_only",
                "approver_identity": None,
                "sandbox_runtime": "test",
                "attempts": [],
                "outcome": "succeeded",
                "duration_ms": 1.0,
                "previous_hash": "0" * 64,
                "record_hash": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AuditIntegrityError):
        verify_audit_file(path)


@pytest.mark.asyncio
async def test_tool_call_writes_terminal_audit_record(tmp_path: Path) -> None:
    """The dispatcher records a completed call with its lifecycle attempts."""

    database_path = tmp_path / "gateway.sqlite"
    await seed_database(database_path)
    settings = Settings(
        environment="test",
        database_path=database_path,
        state_store_backend="memory",
    )
    state_store = InMemoryStateStore()
    logger = AuditLogger(tmp_path / "audit.jsonl")
    result = await execute_tool_call(
        create_registry(settings),
        state_store,
        create_tracing(),
        "session-1",
        types.CallToolRequestParams(
            name="db_query", arguments={"query": "SELECT order_id FROM orders"}
        ),
        audit_logger=logger,
        sandbox_runtime="test",
    )
    assert result.is_error is False
    assert verify_audit_file(tmp_path / "audit.jsonl") is True
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert record["tool_name"] == "db_query"
    assert record["policy_input"]["tool_name"] == "db_query"
    assert record["outcome"] == "succeeded"
    assert record["attempts"][0]["outcome"] == "succeeded"
