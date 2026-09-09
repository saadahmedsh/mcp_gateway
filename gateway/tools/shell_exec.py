"""Declared shell tool that remains disabled until sandboxing is available."""

from pydantic import Field

from gateway.errors import ToolNotEnabledError
from gateway.models import RiskClass, StrictModel
from gateway.registry import ToolDefinition


class ShellExecInput(StrictModel):
    """Arguments reserved for the future sandboxed shell executor."""

    command: str = Field(min_length=1, max_length=10_000)


class ShellExecOutput(StrictModel):
    """Output shape reserved for Phase 4 shell execution."""

    exit_code: int
    stdout: str
    stderr: str


async def shell_exec_disabled(_request: ShellExecInput) -> ShellExecOutput:
    """Refuse all shell execution until Phase 4 provides isolation."""

    raise ToolNotEnabledError(
        "shell_exec is not enabled until Phase 4 sandboxing is implemented"
    )


def create_shell_exec_tool() -> ToolDefinition[ShellExecInput, ShellExecOutput]:
    """Create the registered but intentionally unavailable shell tool."""

    return ToolDefinition(
        name="shell_exec",
        description="Run a shell command (disabled until Phase 4 sandboxing)",
        risk_class=RiskClass.DESTRUCTIVE,
        input_model=ShellExecInput,
        output_model=ShellExecOutput,
        handler=shell_exec_disabled,
    )
