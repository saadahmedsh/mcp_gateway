"""Tests for the typed tool registry."""

from pathlib import Path

import pytest

from gateway.errors import ToolInputError
from gateway.server import create_registry
from gateway.tools.db_query import DbQueryInput


def test_registry_exports_pydantic_input_schema(tmp_path: Path) -> None:
    """Verify that registry schemas come directly from Pydantic models."""

    from gateway.config import Settings

    registry = create_registry(Settings(database_path=tmp_path / "gateway.sqlite"))

    definition = registry.get("db_query")

    assert definition.input_schema == DbQueryInput.model_json_schema()


@pytest.mark.asyncio
async def test_registry_rejects_invalid_arguments(tmp_path: Path) -> None:
    """Verify that registry validation failures retain structured details."""

    from gateway.config import Settings

    registry = create_registry(Settings(database_path=tmp_path / "gateway.sqlite"))

    with pytest.raises(ToolInputError) as captured:
        await registry.execute("db_query", {"max_rows": 10})

    assert captured.value.response.error.code == "schema_validation"
    assert captured.value.response.error.issues[0].location == ["query"]
