"""Typed tool registration, schema export, validation, and dispatch."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel, ValidationError

from gateway.errors import ToolInputError, ToolNotFoundError
from gateway.models import RiskClass

InputModelT = TypeVar("InputModelT", bound=BaseModel)
OutputModelT = TypeVar("OutputModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ToolDefinition(Generic[InputModelT, OutputModelT]):
    """Complete declaration of one gateway-owned tool."""

    name: str
    description: str
    risk_class: RiskClass
    input_model: type[InputModelT]
    output_model: type[OutputModelT]
    handler: Callable[[InputModelT], Awaitable[OutputModelT]]
    idempotent: bool = False

    @property
    def input_schema(self) -> dict[str, Any]:
        """Return the tool's Pydantic-derived JSON input schema."""

        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        """Return the tool's Pydantic-derived JSON output schema."""

        return self.output_model.model_json_schema()

    async def execute(self, arguments: Mapping[str, Any]) -> OutputModelT:
        """Validate arguments, execute the handler, and validate its output."""

        request = self.validate(arguments)
        return await self.execute_validated(request)

    def validate(self, arguments: Mapping[str, Any]) -> InputModelT:
        """Validate untrusted arguments against the declared input model."""

        try:
            return self.input_model.model_validate(dict(arguments))
        except ValidationError as error:
            raise ToolInputError.from_validation_error(error) from error

    async def execute_validated(self, request: InputModelT) -> OutputModelT:
        """Run a previously validated request and validate its output model."""

        result = await self.handler(request)
        return self.output_model.model_validate(result)


class ToolRegistry:
    """In-memory registry and common dispatch boundary for all tools."""

    def __init__(self) -> None:
        """Initialize an empty deterministic registry."""

        self._definitions: dict[str, ToolDefinition[BaseModel, BaseModel]] = {}

    def register(self, definition: ToolDefinition[InputModelT, OutputModelT]) -> None:
        """Register one tool, rejecting ambiguous duplicate names."""

        if definition.name in self._definitions:
            raise ValueError(f"Tool already registered: {definition.name}")
        erased = cast(ToolDefinition[BaseModel, BaseModel], definition)
        self._definitions[definition.name] = erased

    def list_tools(self) -> tuple[ToolDefinition[BaseModel, BaseModel], ...]:
        """Return definitions in deterministic registration order."""

        return tuple(self._definitions.values())

    def get(self, name: str) -> ToolDefinition[BaseModel, BaseModel]:
        """Return a registered definition or raise a typed error."""

        try:
            return self._definitions[name]
        except KeyError as error:
            raise ToolNotFoundError(name) from error

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> BaseModel:
        """Execute a registered tool through the shared validation boundary."""

        return await self.get(name).execute(arguments)

    def validate(self, name: str, arguments: Mapping[str, Any]) -> BaseModel:
        """Validate one tool request without invoking its handler."""

        return self.get(name).validate(arguments)

    async def execute_validated(self, name: str, request: BaseModel) -> BaseModel:
        """Execute a validated request through the registered handler."""

        return await self.get(name).execute_validated(request)
