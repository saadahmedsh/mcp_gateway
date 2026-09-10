"""Command-line approver for pending Redis-backed requests."""

import argparse
import asyncio

from gateway.config import get_settings
from gateway.hitl.queue import RedisApprovalQueue


async def _run() -> None:
    """List pending requests and resolve one selected by the operator."""

    settings = get_settings()
    queue = RedisApprovalQueue(str(settings.redis_url))
    try:
        pending = await queue.list_pending()
        for request in pending:
            print(
                f"{request.approval_id} tool={request.tool_name} "
                f"arguments={request.arguments} rule={request.matched_rule}"
            )
        parser = argparse.ArgumentParser()
        parser.add_argument("approval_id")
        parser.add_argument("decision", choices=("approve", "reject"))
        parser.add_argument("--approver", required=True)
        parser.add_argument("--reason", required=True)
        args = parser.parse_args()
        await queue.decide(
            args.approval_id,
            args.decision == "approve",
            args.approver,
            args.reason,
        )
    finally:
        await queue.close()


def main() -> None:
    """Run the approval CLI."""

    asyncio.run(_run())
