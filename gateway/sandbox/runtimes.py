"""Docker runtime command construction for sandbox execution."""

from enum import StrEnum


class SandboxRuntime(StrEnum):
    """Supported container isolation runtimes."""

    GVISOR = "gvisor"
    HARDENED_DOCKER = "hardened-docker"


def runtime_arguments(runtime: SandboxRuntime) -> list[str]:
    """Return Docker flags for the selected isolation runtime."""

    if runtime is SandboxRuntime.GVISOR:
        return ["--runtime", "runsc"]
    return ["--security-opt", "no-new-privileges"]
