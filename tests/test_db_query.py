"""Tests for deterministic, physically read-only SQLite execution."""

from pathlib import Path

import pytest

from gateway.errors import ToolExecutionError
from gateway.tools.db_query import DbQueryInput, query_database, seed_database


@pytest.mark.asyncio
async def test_db_query_returns_seeded_rows(tmp_path: Path) -> None:
    """Verify that parameterized queries return deterministic synthetic rows."""

    database_path = tmp_path / "gateway.sqlite"
    await seed_database(database_path)

    result = await query_database(
        database_path,
        DbQueryInput(
            query=(
                "SELECT order_id, customer_name FROM orders "
                "WHERE status = :status ORDER BY order_id"
            ),
            parameters={"status": "pending"},
        ),
    )

    assert result.row_count == 1
    assert result.rows == [{"order_id": "ORD-1001", "customer_name": "Max Mustermann"}]
    assert result.truncated is False


@pytest.mark.asyncio
async def test_db_query_is_physically_read_only(tmp_path: Path) -> None:
    """Verify that SQLite rejects mutation attempts independently of policy."""

    database_path = tmp_path / "gateway.sqlite"
    await seed_database(database_path)

    with pytest.raises(ToolExecutionError, match="SQLite rejected the query"):
        await query_database(
            database_path,
            DbQueryInput(
                query="DELETE FROM orders",
            ),
        )


@pytest.mark.asyncio
async def test_db_query_caps_returned_rows(tmp_path: Path) -> None:
    """Verify that database results cannot exceed the requested row limit."""

    database_path = tmp_path / "gateway.sqlite"
    await seed_database(database_path)

    result = await query_database(
        database_path,
        DbQueryInput(query="SELECT order_id FROM orders ORDER BY order_id", max_rows=2),
    )

    assert result.row_count == 2
    assert result.truncated is True
