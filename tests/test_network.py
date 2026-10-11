import asyncio
from contextlib import asynccontextmanager

import httpcore
import httpx
import pytest
from telegram.error import TimedOut

from vocabulario.network import exception_names, telegram_request


@asynccontextmanager
async def endpoint(*, stall=False):
    received, tasks = [], set()
    release = asyncio.Event()

    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            received.append(headers)
            if stall:
                await release.wait()
            else:
                body = b'{"ok":true,"result":true}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/sendMessage", received
    finally:
        release.set()
        server.close()
        if tasks:
            for task in list(tasks):
                task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2)
        await asyncio.wait_for(server.wait_closed(), timeout=2)


async def test_failed_connection_is_retried_without_duplicate_submission(monkeypatch):
    async with telegram_request() as request, endpoint() as (url, received):
        backend = request._client._transport._pool._network_backend
        connect = backend.connect_tcp
        attempts = 0

        async def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpcore.ConnectTimeout("simulated connection failure")
            return await connect(*args, **kwargs)

        monkeypatch.setattr(backend, "connect_tcp", fail_once)
        assert await asyncio.wait_for(request.post(url), timeout=5) is True
        assert attempts == 2
        assert len(received) == 1


async def test_read_timeout_after_submission_does_not_replay_message():
    async with telegram_request() as request, endpoint(stall=True) as (url, received):
        with pytest.raises(TimedOut):
            await asyncio.wait_for(request.post(url, read_timeout=0.05), timeout=3)
        assert len(received) == 1


def test_error_diagnostics_include_phase_without_private_url_or_token():
    error = TimedOut()
    error.__cause__ = httpx.ConnectTimeout("https://example.test/private-token")
    assert exception_names(error) == "TimedOut <- ConnectTimeout"
    assert "private-token" not in exception_names(error)


def test_error_diagnostics_handle_cycles():
    error = RuntimeError("private contents")
    error.__cause__ = error
    assert exception_names(error) == "RuntimeError"
