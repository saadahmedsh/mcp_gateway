"""Restrictive resource and capability profiles for tool containers."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    """Limits and mounts applied to one isolated tool execution."""

    name: str
    timeout_seconds: float
    memory: str
    cpus: float
    pids_limit: int
    network: bool
    workspace: Path
    mounts: tuple[tuple[Path, str, bool], ...] = ()


def profile_for_tool(tool_name: str, workspace: Path) -> SandboxProfile:
    """Return the least-privileged profile for a registered tool."""

    if tool_name == "db_query":
        return SandboxProfile(
            name="db_query",
            timeout_seconds=10.0,
            memory="256m",
            cpus=1.0,
            pids_limit=32,
            network=False,
            workspace=workspace,
        )
    if tool_name == "shell_exec":
        return SandboxProfile(
            name="shell_exec",
            timeout_seconds=5.0,
            memory="128m",
            cpus=0.5,
            pids_limit=16,
            network=False,
            workspace=workspace,
        )
    raise ValueError(f"No sandbox profile exists for {tool_name}")
