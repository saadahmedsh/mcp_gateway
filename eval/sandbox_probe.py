"""Run one real local sandbox smoke test."""

import asyncio
import os
from pathlib import Path

from gateway.sandbox.profiles import profile_for_tool
from gateway.sandbox.runner import SandboxRunner


async def main() -> None:
    """Run a bounded command and print its captured output."""

    runner = SandboxRunner(os.environ.get("GATEWAY_SANDBOX_RUNTIME", "hardened-docker"))
    result = await runner.run(
        ["python", "-c", "print('sandbox-ok')"],
        profile_for_tool("shell_exec", Path(".")),
    )
    print({"exit_code": result.exit_code, "stdout": result.stdout.strip()})


if __name__ == "__main__":
    asyncio.run(main())
