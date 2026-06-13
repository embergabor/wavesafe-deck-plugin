"""Functional tests: the REAL backend (`main.py` + `mpd_client.py`) against a
REAL MPD daemon playing REAL tagged FLAC files.

Requires `mpd` and `flac` on PATH (macOS: `brew install mpd flac`); the whole
module skips cleanly when they're missing. Differences from the unit suite
(tests/): real tag extraction, real playback/seek timing (null output runs at
realtime), sticker SQLite persistence across daemon restarts, state_file
queue restore, and byte-exact `readpicture` of embedded art.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
for _p in (_PKG, _HERE, os.path.join(_PKG, "tests", "stubs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import base64
import unittest

import decky  # stub from tests/stubs
import main
from mpd_client import MPDClient

from media_gen import FLAC_BIN, build_test_library
from mpd_harness import MPD_BIN, MPDHarness

HAVE_DEPS = bool(MPD_BIN and FLAC_BIN)
SKIP_REASON = "needs `mpd` and `flac` on PATH (brew install mpd flac)"


async def connect(harness: MPDHarness) -> MPDClient:
    c = MPDClient(harness.socket)
    await c.connect()
    return c


async def wait_for_db(client: MPDClient, songs: int, timeout: float = 15.0) -> None:
    """Trigger a scan and wait until MPD's database holds `songs` songs."""
    await client.command("update")
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        st = MPDClient.as_dict(await client.command("status"))
        stats = MPDClient.as_dict(await client.command("stats"))
        if "updating_db" not in st and int(stats.get("songs", "0")) >= songs:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"MPD scan never reached {songs} songs")


async def make_plugin(harness: MPDHarness, client: MPDClient) -> main.Plugin:
    p = main.Plugin()
    main.S = main._State()
    main.S.cmd = client
    main.S.socket = harness.socket
    main.S.host, main.S.port = "127.0.0.1", 0
    await main._build_index()
    return p


async def poll(predicate, timeout: float = 5.0, interval: float = 0.02):
    """Await an async predicate until truthy; returns its value or None."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    return None


@unittest.skipUnless(HAVE_DEPS, SKIP_REASON)
class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    """One shared daemon; each test resets transport state in asyncSetUp."""

    harness: MPDHarness
    manifest: dict

    @classmethod
    def setUpClass(cls):
        cls.harness = MPDHarness()
        cls.manifest = build_test_library(cls.harness.music_dir)
        cls.harness.start()

    @classmethod
    def tearDownClass(cls):
        cls.harness.cleanup()

    async def asyncSetUp(self):
        self.client = await connect(self.harness)
        await wait_for_db(self.client, songs=3)
        self.plugin = await make_plugin(self.harness, self.client)
        await self.client.command("stop")
        await self.client.command("clear")
        await self.client.command("random 0")
        await self.client.command("replay_gain_mode album")

    async def asyncTearDown(self):
        await self.client.close()

    async def test_daemon_boots_from_shipped_template(self):
        self.assertTrue(self.client.version, "no MPD version — daemon not speaking protocol")

    async def test_real_tags_indexed(self):
        albums = main.S.albums
        self.assertEqual(set(albums), {self.manifest["abbey_key"], self.manifest["halo_key"]})
        abbey = albums[self.manifest["abbey_key"]]
        # Real TRACKNUMBER tags must reorder the on-disk "two before one" encode order.
        self.assertEqual(abbey.uris, self.manifest["abbey_uris"])
        self.assertEqual((abbey.genre, abbey.year), ("Rock", 1969))
        halo = albums[self.manifest["halo_key"]]
        self.assertEqual(halo.genre, "ost_game")  # the custom Soundtrack convention, end to end

    async def test_play_album_really_plays(self):
        await self.plugin.play_album(self.manifest["halo_key"], 0)
        st = await self.plugin.status()
        self.assertEqual(st["state"], "play")
        self.assertEqual(st["current"]["uri"], "ost/halo/theme.flac")
        self.assertAlmostEqual(st["durationSec"], 3.0, delta=0.2)

    async def test_auto_advance_through_album(self):
        # Two real 0.3s tracks; null output plays at realtime → MPD itself must
        # advance from track 0 to track 1 by decoding to the end.
        await self.plugin.play_album(self.manifest["abbey_key"], 0)

        async def advanced():
            st = await self.plugin.status()
            return st["songPos"] == 1

        self.assertIsNotNone(await poll(advanced, timeout=5.0), "never advanced to track 2")

    async def test_seek_and_elapsed(self):
        await self.plugin.play_album(self.manifest["halo_key"], 0)
        await self.plugin.seek(1.5)
        st = await self.plugin.status()
        self.assertGreaterEqual(st["elapsedSec"], 1.4)
        self.assertEqual(st["state"], "play")

    async def test_shuffle_and_replaygain_roundtrip(self):
        self.assertFalse((await self.plugin.status())["random"])
        await self.plugin.toggle_shuffle()
        self.assertTrue((await self.plugin.status())["random"])
        await self.plugin.set_replay_gain("off")
        self.assertEqual((await self.plugin.status())["replayGainMode"], "off")
        await self.plugin.set_replay_gain("album")
        self.assertEqual((await self.plugin.status())["replayGainMode"], "album")

    async def test_cover_art_byte_exact(self):
        data_url = await self.plugin.cover_art("beatles/abbey/one.flac")
        self.assertIsNotNone(data_url, "no embedded art returned")
        header, b64 = data_url.split(",", 1)
        self.assertIn("image/png", header)
        self.assertEqual(base64.b64decode(b64), self.manifest["png"])

    async def test_idle_loop_emits_on_real_play(self):
        decky.emitted.clear()
        task = asyncio.create_task(main._idle_loop())
        try:
            await asyncio.sleep(0.2)  # let the loop enter idle
            await self.plugin.play_album(self.manifest["halo_key"], 0)

            async def got_event():
                return any(e == main.PLAYER_EVENT for e, _ in decky.emitted)

            self.assertIsNotNone(await poll(got_event, timeout=5.0), "no player event emitted")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@unittest.skipUnless(HAVE_DEPS, SKIP_REASON)
class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Own daemon per test — these restart MPD and inspect what survives."""

    async def asyncSetUp(self):
        self.harness = MPDHarness()
        self.manifest = build_test_library(self.harness.music_dir)
        self.harness.start()
        self.client = await connect(self.harness)
        await wait_for_db(self.client, songs=3)
        self.plugin = await make_plugin(self.harness, self.client)

    async def asyncTearDown(self):
        await self.client.close()
        self.harness.cleanup()

    async def _reconnect(self):
        await self.client.close()
        self.harness.restart()
        self.client = await connect(self.harness)
        self.plugin = await make_plugin(self.harness, self.client)

    async def test_favorites_survive_daemon_restart(self):
        key = self.manifest["abbey_key"]
        self.assertTrue(await self.plugin.toggle_favorite(key))
        await self._reconnect()
        self.assertTrue(await self.plugin.is_favorite(key), "sticker lost across restart")
        favs = await self.plugin.favorite_albums()
        self.assertEqual([a["key"] for a in favs], [key])

    async def test_queue_survives_daemon_restart(self):
        await self.plugin.play_album(self.manifest["abbey_key"], 0)
        await self.client.command("pause 1")
        await self._reconnect()
        st = MPDClient.as_dict(await self.client.command("status"))
        # state_file + restore_paused "yes": queue is back and not auto-playing.
        self.assertEqual(st.get("playlistlength"), "2", "queue not restored from state_file")
        self.assertNotEqual(st.get("state"), "play")


@unittest.skipUnless(HAVE_DEPS, SKIP_REASON)
class PowerCutTests(unittest.IsolatedAsyncioTestCase):
    """state_file_interval: the queue must survive a HARD power cut (SIGKILL —
    no clean-shutdown state write), via the periodic autosave."""

    async def asyncSetUp(self):
        # 1s autosave so the test doesn't sit through the shipped 60s.
        self.harness = MPDHarness(state_file_interval="1")
        self.manifest = build_test_library(self.harness.music_dir)
        self.harness.start()
        self.client = await connect(self.harness)
        await wait_for_db(self.client, songs=3)
        self.plugin = await make_plugin(self.harness, self.client)

    async def asyncTearDown(self):
        await self.client.close()
        self.harness.cleanup()

    async def test_queue_survives_hard_kill(self):
        await self.plugin.play_album(self.manifest["abbey_key"], 0)
        await self.client.command("pause 1")
        # Wait for the autosave to land (interval 1s; poll the state file).
        state_path = os.path.join(self.harness.data_dir, "state")

        def state_saved():
            try:
                with open(state_path) as f:
                    return "playlist_begin" in f.read()
            except OSError:
                return False

        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline and not state_saved():
            await asyncio.sleep(0.2)
        self.assertTrue(state_saved(), "state file never autosaved with a queue")

        await self.client.close()
        self.harness.kill_hard()  # power cut — no shutdown write
        if os.path.exists(self.harness.socket):
            os.unlink(self.harness.socket)
        self.harness.start()
        self.client = await connect(self.harness)
        st = MPDClient.as_dict(await self.client.command("status"))
        self.assertEqual(st.get("playlistlength"), "2", "queue lost across a hard power cut")
        self.assertNotEqual(st.get("state"), "play")


if __name__ == "__main__":
    unittest.main()
