"""Read-only, parameterized SQL execution against synthetic SQLite data."""

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from pydantic import Field

from gateway.errors import ToolExecutionError
from gateway.models import RiskClass, StrictModel
from gateway.registry import ToolDefinition

if TYPE_CHECKING:
    from gateway.sandbox.runner import SandboxRunner

SqlScalar: TypeAlias = str | int | float | None
SqlParameters: TypeAlias = list[SqlScalar] | dict[str, SqlScalar]

_WRITE_ACTIONS = frozenset(
    action
    for action in (
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_UPDATE,
    )
)


class DbQueryInput(StrictModel):
    """Arguments accepted by the read-only database tool."""

    query: str = Field(min_length=1, max_length=10_000)
    parameters: SqlParameters = Field(default_factory=dict)
    max_rows: int = Field(default=100, ge=1, le=500)


class DbQueryOutput(StrictModel):
    """JSON-safe tabular result returned by the database tool."""

    columns: list[str]
    rows: list[dict[str, SqlScalar]]
    row_count: int = Field(ge=0)
    truncated: bool


def _seed_database(database_path: Path) -> None:
    """Create the synthetic SQLite fixture synchronously and idempotently."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                status TEXT NOT NULL,
                total_cents INTEGER NOT NULL
            )
            """)
        connection.executemany(
            """
            INSERT OR IGNORE INTO orders (
                order_id, customer_name, customer_email, status, total_cents
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                ("ORD-1001", "Max Mustermann", "user@example.com", "pending", 12900),
                ("ORD-1002", "Erika Musterfrau", "erika@example.com", "shipped", 4599),
                ("ORD-1003", "Alex Example", "alex@example.com", "cancelled", 8750),
            ),
        )
        connection.commit()


async def seed_database(database_path: Path) -> None:
    """Create the deterministic synthetic database without blocking the event loop."""

    await asyncio.to_thread(_seed_database, database_path)


def _read_only_authorizer(
    action: int,
    _argument_one: str | None,
    _argument_two: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    """Reject SQLite operations that could mutate database state."""

    return sqlite3.SQLITE_DENY if action in _WRITE_ACTIONS else sqlite3.SQLITE_OK


def _normalize_row(columns: Sequence[str], row: sqlite3.Row) -> dict[str, SqlScalar]:
    """Convert one SQLite row into a JSON-safe mapping."""

    normalized: dict[str, SqlScalar] = {}
    for column, value in zip(columns, row, strict=True):
        if value is not None and not isinstance(value, str | int | float):
            raise ToolExecutionError(
                f"Column {column!r} returned an unsupported SQLite value type"
            )
        normalized[column] = value
    return normalized


def _execute_query(database_path: Path, request: DbQueryInput) -> DbQueryOutput:
    """Execute one bounded query through a synchronous read-only connection."""

    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.set_authorizer(_read_only_authorizer)
            cursor = connection.execute(request.query, request.parameters)
            if cursor.description is None:
                raise ToolExecutionError("The SQL statement did not return rows")
            columns = [column[0] for column in cursor.description]
            fetched = cursor.fetchmany(request.max_rows + 1)
    except ToolExecutionError:
        raise
    except sqlite3.Error as error:
        raise ToolExecutionError(f"SQLite rejected the query: {error}") from error

    truncated = len(fetched) > request.max_rows
    visible_rows = fetched[: request.max_rows]
    rows = [_normalize_row(columns, row) for row in visible_rows]
    return DbQueryOutput(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )


async def query_database(database_path: Path, request: DbQueryInput) -> DbQueryOutput:
    """Execute a read-only query without blocking the async request path."""

    return await asyncio.to_thread(_execute_query, database_path, request)


def create_db_query_tool(
    database_path: Path,
    runner: "SandboxRunner | None" = None,
) -> ToolDefinition[DbQueryInput, DbQueryOutput]:
    """Create a database tool bound to a configured SQLite file."""

    async def handler(request: DbQueryInput) -> DbQueryOutput:
        """Execute a validated request against the bound database path."""

        if runner is not None:
            from gateway.sandbox.profiles import profile_for_tool

            result = await runner.run_worker(
                {
                    "kind": "db_query",
                    "database_path": "/data/gateway.sqlite",
                    "request": request.model_dump(mode="json"),
                },
                profile_for_tool("db_query", Path(".")),
                mounts=((database_path, "/data/gateway.sqlite", True),),
            )
            return DbQueryOutput.model_validate(result)
        return await query_database(database_path, request)

    return ToolDefinition(
        name="db_query",
        description="Run one read-only, parameterized query against synthetic orders",
        risk_class=RiskClass.READ_ONLY,
        input_model=DbQueryInput,
        output_model=DbQueryOutput,
        handler=handler,
        idempotent=True,
    )
