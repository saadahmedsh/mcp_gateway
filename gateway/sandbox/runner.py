"""Per-call Docker sandbox lifecycle and resource enforcement."""

import asyncio
import json
import os
import shlex
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import structlog

from gateway.errors import SandboxError, SandboxTimeoutError
from gateway.sandbox.profiles import SandboxProfile
from gateway.sandbox.runtimes import SandboxRuntime, runtime_arguments


class SandboxResult:
    """Captured output and exit status from one sandbox process."""

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        """Initialize a bounded sandbox result."""

        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class SandboxRunner:
    """Run commands with a fresh, restricted Docker container per call."""

    def __init__(
        self,
        runtime: str = "hardened-docker",
        image: str = "mcp-gateway-tool:local",
        output_limit_bytes: int = 1_048_576,
    ) -> None:
        """Configure the runtime, image, and output cap."""

        self._runtime = SandboxRuntime(runtime)
        self._image = image
        self._output_limit = output_limit_bytes
        self._logger = structlog.get_logger()

    async def run(
        self,
        command: Sequence[str],
        profile: SandboxProfile,
        mounts: Sequence[tuple[Path, str, bool]] = (),
        stdin: bytes | None = None,
    ) -> SandboxResult:
        """Run one command and always attempt to reap its named container."""

        container_name = f"mcp-tool-{uuid4().hex}"
        docker_command = [
            "docker",
            "run",
            "--name",
            container_name,
            "--rm",
            "--interactive",
            "--init",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--network=none" if not profile.network else "--network=bridge",
            "--memory",
            profile.memory,
            "--cpus",
            str(profile.cpus),
            "--pids-limit",
            str(profile.pids_limit),
            "--tmpfs",
            "/workspace:rw,noexec,nosuid,nodev",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONPATH=/opt/gateway",
        ]
        docker_command.extend(runtime_arguments(self._runtime))
        if self._runtime is SandboxRuntime.HARDENED_DOCKER:
            docker_command.extend(
                [
                    "--security-opt",
                    f"seccomp={(Path.cwd() / 'docker/seccomp/tool.json').resolve()}",
                ]
            )
        docker_command.extend(["--label", "mcp-gateway.sandbox=true"])
        for host_path, container_path, read_only in mounts:
            mode = "ro" if read_only else "rw"
            docker_command.extend(
                [
                    "--mount",
                    f"type=bind,src={host_path.resolve()},dst={container_path},{mode}",
                ]
            )
        docker_command.extend([self._image, *command])
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *docker_command,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=stdin), timeout=profile.timeout_seconds
                )
            except TimeoutError as error:
                self._logger.warning(
                    "sandbox_security_event",
                    event_type="sandbox_timeout",
                    command=shlex.join(command),
                )
                process.kill()
                await process.wait()
                raise SandboxTimeoutError() from error
            if len(stdout) > self._output_limit or len(stderr) > self._output_limit:
                self._logger.warning(
                    "sandbox_security_event",
                    event_type="output_limit_exceeded",
                    command=shlex.join(command),
                )
                raise SandboxError("Sandbox output exceeded the configured limit")
            result = SandboxResult(
                process.returncode or 0,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            )
            if result.exit_code != 0:
                self._logger.warning(
                    "sandbox_security_event",
                    event_type="sandbox_command_blocked",
                    command=shlex.join(command),
                    stderr=result.stderr.strip(),
                )
                raise SandboxError(
                    f"Sandbox command failed with exit code {result.exit_code}: "
                    f"{shlex.join(command)}; {result.stderr.strip()}"
                )
            return result
        except FileNotFoundError as error:
            raise SandboxError("Docker executable is unavailable") from error
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "-f",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()
            except OSError:
                self._logger.warning(
                    "sandbox_cleanup_unavailable",
                    container_name=container_name,
                )

    async def run_worker(
        self,
        payload: dict[str, object],
        profile: SandboxProfile,
        mounts: Sequence[tuple[Path, str, bool]] = (),
    ) -> dict[str, object]:
        """Run the container-side JSON worker and decode its bounded output."""

        result = await self.run(
            ["python", "-m", "gateway.sandbox.worker"],
            profile,
            mounts,
            stdin=json.dumps(payload).encode(),
        )
        decoded = json.loads(result.stdout)
        if not isinstance(decoded, dict):
            raise SandboxError("Sandbox worker returned an invalid JSON object")
        return decoded
