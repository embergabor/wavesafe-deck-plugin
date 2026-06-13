"""Tests for the dependency-free async MPD client (mpd_client.py)."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import unittest

from fake_mpd import FakeMPDServer
from mpd_client import MPDClient, MPDError, mpd_quote


class MPDClientTests(unittest.IsolatedAsyncioTestCase):
    async def _client(self, server: FakeMPDServer) -> MPDClient:
        c = MPDClient(None, "127.0.0.1", server.port)
        await c.connect()
        return c

    async def test_greeting_version(self):
        async with FakeMPDServer(greeting=b"OK MPD 0.23.5\n") as srv:
            c = await self._client(srv)
            self.assertEqual(c.version, "0.23.5")
            await c.close()

    async def test_bad_greeting_raises(self):
        async with FakeMPDServer(greeting=b"NOPE\n") as srv:
            c = MPDClient(None, "127.0.0.1", srv.port)
            with self.assertRaises(MPDError):
                await c.connect()

    async def test_status_and_currentsong_parse(self):
        responses = {
            "status": b"volume: 80\nstate: play\nsong: 2\nelapsed: 12.500\n"
            b"duration: 200.000\nrandom: 1\nrepeat: 0\nreplay_gain_mode: album\nOK\n",
            "currentsong": b"file: a/b.flac\nTitle: Hello\nArtist: Foo\nAlbum: Bar\n"
            b"AlbumArtist: Foo\nGenre: ost_game\nDate: 1999-10-04\nTrack: 3\nDisc: 1\nTime: 200\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            c = await self._client(srv)
            st = MPDClient.as_dict(await c.command("status"))
            self.assertEqual(st["state"], "play")
            self.assertEqual(st["elapsed"], "12.500")
            self.assertEqual(st["random"], "1")
            song = MPDClient.as_dict(await c.command("currentsong"))
            self.assertEqual(song["file"], "a/b.flac")
            self.assertEqual(song["Genre"], "ost_game")
            await c.close()

    async def test_split_objects(self):
        responses = {
            'find "album" "Bar"': b"file: a/1.flac\nTitle: One\nTrack: 1\n"
            b"file: a/2.flac\nTitle: Two\nTrack: 2\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            c = await self._client(srv)
            objs = MPDClient.split_objects(await c.command('find "album" "Bar"'), "file")
            self.assertEqual(len(objs), 2)
            self.assertEqual(objs[0]["Title"], "One")
            self.assertEqual(objs[1]["Track"], "2")
            await c.close()

    async def test_binary_readpicture(self):
        blob = bytes(range(0, 10))
        responses = {
            'readpicture "a/b.flac" 0': b"size: 10\ntype: image/png\nbinary: 10\n" + blob + b"\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            c = await self._client(srv)
            pairs, data = await c.command_binary('readpicture ' + mpd_quote("a/b.flac") + " 0")
            meta = MPDClient.as_dict(pairs)
            self.assertEqual(meta["size"], "10")
            self.assertEqual(meta["type"], "image/png")
            self.assertEqual(data, blob)
            await c.close()

    async def test_idle_returns_changed_subsystems(self):
        responses = {"idle player mixer options": b"changed: player\nOK\n"}
        async with FakeMPDServer(responses) as srv:
            c = await self._client(srv)
            changed = await c.idle("player", "mixer", "options")
            self.assertEqual(changed, ["player"])
            await c.close()

    async def test_ack_raises_mpderror(self):
        responses = {"badcmd": b'ACK [5@0] {} unknown command "badcmd"\n'}
        async with FakeMPDServer(responses) as srv:
            c = await self._client(srv)
            with self.assertRaises(MPDError) as ctx:
                await c.command("badcmd")
            self.assertIn("unknown command", str(ctx.exception))
            await c.close()

    def test_quote_escapes(self):
        self.assertEqual(mpd_quote('a "b" \\c'), '"a \\"b\\" \\\\c"')
        self.assertEqual(mpd_quote("plain"), '"plain"')


if __name__ == "__main__":
    unittest.main()
