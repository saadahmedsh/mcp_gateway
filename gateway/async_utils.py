"""Async helpers for running bounded synchronous operations safely."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from itertools import count
from typing import ParamSpec, TypeVar

Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")

_BLOCKING_EXECUTOR = ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="mcp-gateway-blocking",
)


async def run_blocking(
    function: Callable[Parameters, Result],
    *args: Parameters.args,
    **kwargs: Parameters.kwargs,
) -> Result:
    """Run a synchronous function without using the loop default executor."""

    future: Future[Result] = _BLOCKING_EXECUTOR.submit(
        partial(function, *args, **kwargs)
    )
    try:
        for _ in count():
            if future.done():
                break
            await asyncio.sleep(0.005)
    except asyncio.CancelledError:
        future.cancel()
        raise
    return future.result()
