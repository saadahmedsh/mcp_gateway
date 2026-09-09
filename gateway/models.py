"""Shared gateway data models."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects undeclared fields and implicit coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RiskClass(StrEnum):
    """Declared operational risk associated with a tool."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


class ErrorIssue(StrictModel):
    """One machine-readable validation issue."""

    location: list[str | int]
    error_type: str
    message: str


class ErrorDetail(StrictModel):
    """Stable error payload returned through MCP."""

    code: str
    message: str
    issues: list[ErrorIssue] = Field(default_factory=list)


class ErrorResponse(StrictModel):
    """Top-level error response for an unsuccessful tool call."""

    error: ErrorDetail
