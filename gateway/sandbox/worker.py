"""Container-side worker entry points for sandboxed tools."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from gateway.tools.db_query import DbQueryInput, query_database


def main() -> None:
    """Execute a narrowly scoped worker command from JSON stdin."""

    payload = json.load(sys.stdin)
    if payload["kind"] == "shell_exec":
        completed = subprocess.run(
            payload["command"],
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        json.dump(
            {
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
            sys.stdout,
        )
        return
    if payload["kind"] == "db_query":
        request = DbQueryInput.model_validate(payload["request"])
        output = asyncio.run(
            query_database(Path(str(payload["database_path"])), request)
        )
        json.dump(output.model_dump(mode="json"), sys.stdout)
        return
    raise ValueError("Unknown sandbox worker command")


if __name__ == "__main__":
    main()
