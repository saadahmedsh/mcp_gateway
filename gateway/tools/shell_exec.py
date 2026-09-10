"""Shell execution through the Phase 4 sandbox boundary."""

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from gateway.errors import ToolNotEnabledError
from gateway.models import RiskClass, StrictModel
from gateway.registry import ToolDefinition

if TYPE_CHECKING:
    from gateway.sandbox.runner import SandboxRunner


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


def create_shell_exec_tool(
    runner: "SandboxRunner | None" = None,
) -> ToolDefinition[ShellExecInput, ShellExecOutput]:
    """Create a shell tool using an injected sandbox runner."""

    async def handler(request: ShellExecInput) -> ShellExecOutput:
        """Execute a command in the isolated shell profile."""

        if runner is None:
            return await shell_exec_disabled(request)
        from gateway.sandbox.profiles import profile_for_tool

        result = await runner.run_worker(
            {"kind": "shell_exec", "command": request.command},
            profile_for_tool("shell_exec", Path(".")),
        )
        return ShellExecOutput(
            exit_code=int(str(result["exit_code"])),
            stdout=str(result["stdout"]),
            stderr=str(result["stderr"]),
        )

    return ToolDefinition(
        name="shell_exec",
        description="Run a shell command inside an isolated sandbox",
        risk_class=RiskClass.DESTRUCTIVE,
        input_model=ShellExecInput,
        output_model=ShellExecOutput,
        handler=handler,
    )
