"""Command-line approver for pending Redis-backed requests."""

import argparse
import asyncio
import sys

from gateway.config import get_settings
from gateway.hitl.queue import RedisApprovalQueue


async def _run() -> None:
    """List pending requests and resolve one selected by the operator."""

    settings = get_settings()
    queue = RedisApprovalQueue(
        str(settings.redis_url),
        ttl_seconds=settings.state_ttl_seconds,
    )
    try:
        pending = await queue.list_pending()
        for request in sorted(pending, key=lambda item: item.created_at, reverse=True):
            print(
                f"{request.approval_id} tool={request.tool_name} "
                f"created_at={request.created_at.isoformat()} "
                f"arguments={request.arguments} rule={request.matched_rule}"
            )
        parser = argparse.ArgumentParser()
        parser.add_argument("approval_id", help="Approval ID or 'list'")
        parser.add_argument("decision", choices=("approve", "reject"), nargs="?")
        parser.add_argument("--approver")
        parser.add_argument("--reason")
        args = parser.parse_args()
        if args.approval_id == "list":
            return
        pending_ids = {request.approval_id for request in pending}
        if args.approval_id not in pending_ids:
            print(
                f"Unknown or expired approval ID: {args.approval_id}",
                file=sys.stderr,
            )
            return
        if args.decision is None or args.approver is None or args.reason is None:
            parser.error(
                "decision, --approver, and --reason are required for approval changes"
            )
        await queue.decide(
            args.approval_id,
            args.decision == "approve",
            args.approver,
            args.reason,
        )
        print(f"Recorded {args.decision} decision for {args.approval_id}")
    finally:
        await queue.close()


def main() -> None:
    """Run the approval CLI."""

    asyncio.run(_run())


if __name__ == "__main__":
    main()
