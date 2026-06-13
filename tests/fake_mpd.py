"""Reusable in-process fake MPD server for backend tests.

Lets us exercise the real MPDClient / Plugin code against a scripted MPD without
a Deck, MPD, or PipeWire — the only way to test the protocol layer on a
python-only dev box. See tests/README.md.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional, Union

Response = Union[bytes, Callable[[str], bytes]]


class FakeMPDServer:
    """A minimal MPD-protocol server.

    `responses` maps an exact command line → bytes (or a callable taking the
    command and returning bytes). Unmatched commands get `default` (a callable)
    or "OK\\n". Every received command is recorded in `.received`.
    """

    def __init__(
        self,
        responses: Optional[dict[str, Response]] = None,
        default: Optional[Callable[[str], bytes]] = None,
        greeting: bytes = b"OK MPD 0.23.5\n",
    ):
        self.responses = responses or {}
        self.default = default
        self.greeting = greeting
        self.received: list[str] = []
        self._server: Optional[asyncio.AbstractServer] = None
        self._writers: set = set()
        self.port: int = 0

    async def __aenter__(self) -> "FakeMPDServer":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            # Python ≥3.12.1: wait_closed() blocks until every client handler
            # coroutine exits — and tests may leave client connections open. Close
            # them server-side so handlers unblock (3.9 didn't need this).
            for w in list(self._writers):
                try:
                    w.close()
                except Exception:
                    pass
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        writer.write(self.greeting)
        await writer.drain()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                cmd = line.decode().strip()
                self.received.append(cmd)
                resp: Optional[Response] = self.responses.get(cmd)
                if resp is None and self.default is not None:
                    resp = self.default(cmd)
                if callable(resp):
                    resp = resp(cmd)
                if resp is None:
                    resp = b"OK\n"
                writer.write(resp)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
