"""Asyncio-native MCP stdio transport for pipe-based subprocesses."""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from mcp import types
from mcp.shared._stream_protocols import ReadStream, WriteStream
from mcp.shared.message import SessionMessage


@asynccontextmanager
async def asyncio_stdio_server() -> (
    AsyncIterator[
        tuple[ReadStream[SessionMessage | Exception], WriteStream[SessionMessage]]
    ]
):
    """Serve MCP messages over asyncio pipes without AnyIO file worker threads."""

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    read_transport, _ = await loop.connect_read_pipe(
        lambda: reader_protocol,
        sys.stdin.buffer,
    )
    write_protocol = asyncio.streams.FlowControlMixin()
    write_transport, _ = await loop.connect_write_pipe(
        lambda: write_protocol,
        sys.stdout.buffer,
    )
    writer = asyncio.StreamWriter(
        write_transport,
        write_protocol,
        reader,
        loop,
    )
    read_stream_writer, read_stream = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[
        SessionMessage
    ](0)

    async def stdin_reader() -> None:
        """Decode newline-delimited JSON-RPC messages from stdin."""

        async with read_stream_writer:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    message = types.jsonrpc_message_adapter.validate_json(
                        line.decode("utf-8"),
                        by_name=False,
                    )
                except Exception as error:
                    await read_stream_writer.send(error)
                    continue
                await read_stream_writer.send(SessionMessage(message))

    async def stdout_writer() -> None:
        """Encode JSON-RPC messages and flush them to stdout."""

        async with write_stream_reader:
            async for session_message in write_stream_reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True,
                    exclude_unset=True,
                )
                writer.write((payload + "\n").encode("utf-8"))
                await writer.drain()

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(stdin_reader)
            task_group.start_soon(stdout_writer)
            yield read_stream, write_stream
    finally:
        read_transport.close()
        write_transport.close()
        await read_stream.aclose()
        await write_stream.aclose()
        await read_stream_writer.aclose()
        await write_stream_reader.aclose()
