"""Run adversarial sandbox smoke checks against the local Docker runtime."""

import asyncio
from dataclasses import replace
from pathlib import Path

from gateway.errors import SandboxError, SandboxTimeoutError
from gateway.sandbox.profiles import profile_for_tool
from gateway.sandbox.runner import SandboxRunner


async def _expect_blocked(
    runner: SandboxRunner,
    command: list[str],
    label: str,
    timeout_seconds: float = 5.0,
) -> str:
    """Run a command expected to fail or time out inside the sandbox."""

    profile = replace(
        profile_for_tool("shell_exec", Path(".")),
        timeout_seconds=timeout_seconds,
    )
    try:
        await runner.run(command, profile)
    except SandboxTimeoutError:
        return f"{label}: blocked by timeout"
    except SandboxError:
        return f"{label}: blocked by sandbox"
    return f"{label}: FAILED"


async def main() -> None:
    """Run host filesystem, network, and timeout checks."""

    runner = SandboxRunner()
    host_file = (Path.cwd() / "README.md").resolve()
    results = [
        await _expect_blocked(
            runner,
            ["python", "-c", f"open({str(host_file)!r}).read()"],
            "host-file",
        ),
        await _expect_blocked(
            runner,
            [
                "python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://example.com')",
            ],
            "network",
        ),
        await _expect_blocked(
            runner,
            ["python", "-c", "while True: pass"],
            "infinite-loop",
            timeout_seconds=1.0,
        ),
    ]
    print("\n".join(results))


if __name__ == "__main__":
    asyncio.run(main())
