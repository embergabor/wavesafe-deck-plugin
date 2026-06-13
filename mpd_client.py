"""Tiny dependency-free async MPD client.

We deliberately avoid python-mpd2 so the plugin payload stays self-contained
(nothing to pip-install on the read-only SteamOS). Implements just the subset the
QAM plugin needs: text commands, status/currentsong parsing, the `idle` event
loop, binary cover-art reads, and stickers.

MPD protocol recap:
  - Connect → server greets with "OK MPD <version>\n".
  - Send "command args\n"; response is "key: value\n" lines, ended by "OK\n" or
    "ACK [..] {..} message\n".
  - Binary payloads (albumart/readpicture) arrive as a "binary: <n>\n" line
    followed by <n> raw bytes + "\n", then "OK".
"""
from __future__ import annotations

import asyncio
from typing import Optional


class MPDError(Exception):
    pass


def mpd_quote(value: str) -> str:
    """Quote an argument for the MPD protocol (wrap in quotes, escape \\ and ")."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class MPDClient:
    """One connection to MPD. Not safe for concurrent commands — callers should
    serialize via the owning lock (see WaveSafePlugin)."""

    def __init__(self, socket_path: Optional[str], host: str = "127.0.0.1", port: int = 6600):
        self._socket_path = socket_path
        self._host = host
        self._port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self.version: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        if self._socket_path:
            self._reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
        else:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        greeting = await self._reader.readline()
        line = greeting.decode("utf-8", "replace").strip()
        if not line.startswith("OK MPD"):
            raise MPDError(f"Unexpected MPD greeting: {line!r}")
        self.version = line.split(" ", 2)[-1]

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _write(self, command: str) -> None:
        if self._writer is None:
            raise MPDError("not connected")
        self._writer.write((command + "\n").encode("utf-8"))
        await self._writer.drain()

    async def _read_until_done(self) -> tuple[list[tuple[str, str]], Optional[bytes]]:
        """Read a response into (key,value) pairs and an optional binary blob."""
        assert self._reader is not None
        pairs: list[tuple[str, str]] = []
        binary: Optional[bytes] = None
        while True:
            raw = await self._reader.readline()
            if not raw:
                raise MPDError("connection closed by MPD")
            line = raw.decode("utf-8", "replace")
            if line.startswith("OK"):
                return pairs, binary
            if line.startswith("ACK"):
                raise MPDError(line.strip())
            key, _, value = line.partition(": ")
            value = value.rstrip("\n")
            if key == "binary":
                n = int(value)
                blob = await self._reader.readexactly(n)
                await self._reader.readexactly(1)  # trailing newline
                binary = blob
            else:
                pairs.append((key, value))

    async def command(self, command: str) -> list[tuple[str, str]]:
        await self._write(command)
        pairs, _ = await self._read_until_done()
        return pairs

    async def command_binary(self, command: str) -> tuple[list[tuple[str, str]], Optional[bytes]]:
        await self._write(command)
        return await self._read_until_done()

    @staticmethod
    def as_dict(pairs: list[tuple[str, str]]) -> dict[str, str]:
        """Collapse pairs into a dict (last value wins). For single-object responses."""
        return {k: v for k, v in pairs}

    @staticmethod
    def split_objects(pairs: list[tuple[str, str]], delimiter: str) -> list[dict[str, str]]:
        """Split a flat pair list into objects, each starting at `delimiter` (e.g. 'file')."""
        objects: list[dict[str, str]] = []
        current: Optional[dict[str, str]] = None
        for key, value in pairs:
            if key == delimiter:
                current = {}
                objects.append(current)
            if current is not None:
                current[key] = value
        return objects

    async def idle(self, *subsystems: str) -> list[str]:
        """Block until one of the named subsystems changes; return changed names.

        This is the heart of the zero-polling design: the plugin waits here
        instead of polling `status` on a timer.
        """
        cmd = "idle" + ("".join(f" {s}" for s in subsystems))
        pairs = await self.command(cmd)
        return [v for k, v in pairs if k == "changed"]

    async def noidle(self) -> None:
        await self._write("noidle")
