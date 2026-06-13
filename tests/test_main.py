"""Tests for the Decky backend logic (main.py), against a fake MPD server.

`decky` is import-stubbed via tests/stubs."""
import os
import sys

# Make `main`, `mpd_client`, `fake_mpd`, and the `decky` stub importable whether
# run via `python3 -m unittest discover` or directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE, os.path.join(_HERE, "stubs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import shlex
import unittest

import decky  # the stub
import main
from fake_mpd import FakeMPDServer
from mpd_client import MPDClient

LISTALLINFO = (
    b"file: rock/abbey/2.flac\nTitle: Two\nAlbum: Abbey Road\nAlbumArtist: The Beatles\n"
    b"Genre: Rock\nDate: 1969\nDisc: 1\nTrack: 2\nLast-Modified: 2021-01-02T00:00:00Z\n"
    b"file: rock/abbey/1.flac\nTitle: One\nAlbum: Abbey Road\nAlbumArtist: The Beatles\n"
    b"Genre: Rock\nDate: 1969\nDisc: 1\nTrack: 1\nLast-Modified: 2021-01-01T00:00:00Z\n"
    b"file: ost/halo/track.flac\nTitle: Halo Theme\nAlbum: Halo\nAlbumArtist: Marty\n"
    b"Genre: ost_game\nDate: 2001\nTrack: 1\nLast-Modified: 2023-05-05T00:00:00Z\n"
    b"OK\n"
)

# NB: real MPD does NOT report ReplayGain mode in `status` — it has a separate
# `replay_gain_status` command (a fact the functional suite caught; keep the fake
# faithful to the real protocol).
STATUS = (
    b"volume: 80\nstate: play\nsong: 0\nelapsed: 1.000\nduration: 100.000\n"
    b"random: 0\nrepeat: 0\nOK\n"
)
RG_STATUS = b"replay_gain_mode: album\nOK\n"  # key verified against real MPD 0.24
CURRENTSONG = b"file: rock/abbey/1.flac\nTitle: One\nAlbum: Abbey Road\nAlbumArtist: The Beatles\nTime: 100\nOK\n"


def sticker_aware_default(store):
    """A default handler giving the fake server stateful sticker support."""

    def handler(cmd: str) -> bytes:
        try:
            toks = shlex.split(cmd)
        except ValueError:
            return b"OK\n"
        if toks[:2] == ["sticker", "get"] and len(toks) == 5:
            _, _, _typ, uri, key = toks
            if (uri, key) in store:
                return f"sticker: {key}={store[(uri, key)]}\nOK\n".encode()
            return b"ACK [50@0] {sticker} no such sticker\n"
        if toks[:2] == ["sticker", "set"] and len(toks) == 6:
            _, _, _typ, uri, key, val = toks
            store[(uri, key)] = val
            return b"OK\n"
        if toks[:2] == ["sticker", "delete"] and len(toks) == 5:
            _, _, _typ, uri, key = toks
            store.pop((uri, key), None)
            return b"OK\n"
        if toks[:2] == ["sticker", "find"]:
            key = toks[-1]
            out = b""
            for (uri, k), v in store.items():
                if k == key:
                    out += f"file: {uri}\nsticker: {k}={v}\n".encode()
            return out + b"OK\n"
        return b"OK\n"

    return handler


def fresh_state() -> "main._State":
    """_State() owns an asyncio.Lock; on Python 3.9 that needs a current event
    loop even outside async code. Sync tests (config rendering) call this."""
    try:
        return main._State()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        return main._State()


async def make_plugin(srv: FakeMPDServer) -> main.Plugin:
    client = MPDClient(None, "127.0.0.1", srv.port)
    await client.connect()
    # State lives on the module singleton (Decky may call methods unbound).
    main.S = main._State()
    main.S.cmd = client
    main.S.socket = "/nonexistent/mpd.sock"  # reconnects go to host/port
    main.S.host, main.S.port = "127.0.0.1", srv.port
    p = main.Plugin()
    await main._build_index()
    return p


class HelperTests(unittest.TestCase):
    def test_album_key_matches_mpd_core_norm(self):
        self.assertEqual(main.album_key("The Beatles", "Abbey Road"), "thebeatles::abbeyroad")

    def test_int_parsing_handles_slash(self):
        self.assertEqual(main._int("3/12"), 3)
        self.assertIsNone(main._int(None))
        self.assertIsNone(main._int("x"))

    def test_track_from_song(self):
        t = main.track_from_song({"file": "a.flac", "Title": "X", "Track": "5/9", "Disc": "2", "Time": "200"})
        self.assertEqual((t["trackNo"], t["discNo"], t["durationSec"]), (5, 2, 200.0))


class IndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_index_orders_and_aggregates(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            self.assertEqual(set(main.S.albums), {"thebeatles::abbeyroad", "marty::halo"})
            abbey = main.S.albums["thebeatles::abbeyroad"]
            # reordered to (disc, track): 1 before 2 → gapless ordering
            self.assertEqual(abbey.uris, ["rock/abbey/1.flac", "rock/abbey/2.flac"])
            self.assertEqual((abbey.genre, abbey.year), ("Rock", 1969))
            self.assertEqual(main.S.uri_to_album["rock/abbey/1.flac"], "thebeatles::abbeyroad")

    async def test_recent_albums_by_last_modified(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            recents = await p.recent_albums(10)
            self.assertEqual(recents[0]["key"], "marty::halo")  # 2023 newest
            self.assertEqual(recents[0]["genre"], "ost_game")

    async def test_play_album_clears_adds_in_order_plays(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            srv.received.clear()
            await p.play_album("thebeatles::abbeyroad", 0)
            self.assertEqual(
                srv.received,
                [
                    "clear",
                    'add "rock/abbey/1.flac"',
                    'add "rock/abbey/2.flac"',
                    "random 0",  # album order even if shuffle was left on
                    "play 0",
                ],
            )

    async def test_play_album_unknown_raises(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            with self.assertRaises(Exception):
                await p.play_album("nope::nope", 0)

    async def test_play_albums_session_order_and_shuffle_flag(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            srv.received.clear()
            await p.play_albums(["marty::halo", "thebeatles::abbeyroad"], True)
            self.assertEqual(
                srv.received,
                [
                    "clear",
                    'add "ost/halo/track.flac"',
                    'add "rock/abbey/1.flac"',
                    'add "rock/abbey/2.flac"',
                    "random 1",
                    "play 0",
                ],
            )
            srv.received.clear()
            await p.play_albums(["thebeatles::abbeyroad"], False)
            self.assertIn("random 0", srv.received)
            with self.assertRaises(Exception):
                await p.play_albums(["nope::nope"], False)

    async def test_enqueue_album_appends_without_clear_or_play(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            srv.received.clear()
            await p.enqueue_album("thebeatles::abbeyroad")
            self.assertEqual(
                srv.received,
                ['add "rock/abbey/1.flac"', 'add "rock/abbey/2.flac"'],
            )

    async def test_all_albums_sorted_with_metadata(self):
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            albums = await p.all_albums()
            self.assertEqual([a["key"] for a in albums], ["marty::halo", "thebeatles::abbeyroad"])
            self.assertEqual(albums[0]["genre"], "ost_game")
            self.assertEqual(albums[1]["year"], 1969)


class StatusAndCoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_shape(self):
        responses = {
            "listallinfo": LISTALLINFO,
            "status": STATUS,
            "currentsong": CURRENTSONG,
            "replay_gain_status": RG_STATUS,
        }
        async with FakeMPDServer(responses) as srv:
            p = await make_plugin(srv)
            st = await p.status()
            self.assertEqual(st["state"], "play")
            self.assertEqual(st["replayGainMode"], "album")
            self.assertFalse(st["random"])
            self.assertEqual(st["durationSec"], 100.0)
            self.assertEqual(st["current"]["uri"], "rock/abbey/1.flac")

    async def test_cover_art_assembles_chunks(self):
        part1 = bytes(range(0, 6))
        part2 = bytes(range(6, 10))
        responses = {
            "listallinfo": LISTALLINFO,
            'readpicture "rock/abbey/1.flac" 0': b"size: 10\ntype: image/jpeg\nbinary: 6\n" + part1 + b"\nOK\n",
            'readpicture "rock/abbey/1.flac" 6': b"size: 10\ntype: image/jpeg\nbinary: 4\n" + part2 + b"\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            p = await make_plugin(srv)
            data_url = await p.cover_art("rock/abbey/1.flac")
            self.assertIsNotNone(data_url)
            self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
            import base64
            decoded = base64.b64decode(data_url.split(",", 1)[1])
            self.assertEqual(decoded, bytes(range(0, 10)))

    async def test_album_cover_resolves_first_uri(self):
        art = bytes(range(0, 4))
        responses = {
            "listallinfo": LISTALLINFO,
            'readpicture "rock/abbey/1.flac" 0': b"size: 4\ntype: image/png\nbinary: 4\n" + art + b"\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            p = await make_plugin(srv)
            data_url = await p.album_cover("thebeatles::abbeyroad")
            self.assertTrue(data_url.startswith("data:image/png;base64,"))
            self.assertIsNone(await p.album_cover("nope::nope"))


class FavoriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_favorite_roundtrip(self):
        store: dict = {}
        async with FakeMPDServer({"listallinfo": LISTALLINFO}, default=sticker_aware_default(store)) as srv:
            p = await make_plugin(srv)
            key = "thebeatles::abbeyroad"
            self.assertFalse(await p.is_favorite(key))
            self.assertTrue(await p.toggle_favorite(key))   # now favorited
            self.assertTrue(await p.is_favorite(key))
            favs = await p.favorite_albums()
            self.assertEqual([a["key"] for a in favs], [key])
            self.assertFalse(await p.toggle_favorite(key))  # unfavorited
            self.assertEqual(await p.favorite_albums(), [])


class RenderConfigTests(unittest.TestCase):
    """The bundled STATIC mpd gets the pipe output (PCM → host pw-cat); a
    system mpd gets the native pipewire output."""

    def _render(self, mpd_bin):
        import tempfile

        main.S = fresh_state()
        with tempfile.TemporaryDirectory() as td:
            main.S.plugin_dir = td  # no template file → fallback conf used
            main.S.music_dir = "/m"
            main.S.data_dir = "/d"
            main.S.socket = "/s"
            dest = os.path.join(td, "mpd.conf")
            main._render_config(dest, mpd_bin)
            with open(dest) as f:
                return f.read()

    def test_bundled_binary_gets_pipe_output(self):
        import tempfile

        main.S = fresh_state()
        with tempfile.TemporaryDirectory() as td:
            main.S.plugin_dir = td
            main.S.music_dir, main.S.data_dir, main.S.socket = "/m", "/d", "/s"
            dest = os.path.join(td, "mpd.conf")
            main._render_config(dest, os.path.join(td, "bin", "mpd"))
            conf = open(dest).read()
        self.assertIn('type        "pipe"', conf)
        self.assertIn("pw-cat", conf)
        self.assertNotIn('"pipewire"', conf)

    def test_system_binary_gets_pipewire_output(self):
        conf = self._render("/usr/bin/mpd")
        self.assertIn('type        "pipewire"', conf)
        self.assertNotIn("pw-cat", conf)
        self.assertNotIn("{{AUDIO_OUTPUT}}", conf)

    def test_state_survives_power_cuts(self):
        """state_file_interval makes MPD persist queue+playhead periodically —
        a hard power cut must not lose the session (only a stale playhead)."""
        conf = self._render("/usr/bin/mpd")
        self.assertIn("state_file_interval", conf)
        self.assertIn("restore_paused", conf)

    def test_real_template_renders_with_state_interval(self):
        import tempfile

        repo_root = os.path.dirname(_HERE)  # plugin root (tests/ is one level down)
        main.S = fresh_state()
        with tempfile.TemporaryDirectory() as td:
            # package layout: <plugin_dir>/daemon/mpd.conf.tmpl — point at the repo copy
            os.symlink(os.path.join(repo_root, "daemon"), os.path.join(td, "daemon"))
            main.S.plugin_dir = td
            main.S.music_dir, main.S.data_dir, main.S.socket = "/m", "/d", "/s"
            dest = os.path.join(td, "mpd.conf")
            main._render_config(dest, "/usr/bin/mpd")
            conf = open(dest).read()
        self.assertIn("state_file_interval", conf)
        import re
        self.assertIsNone(re.search(r"\{\{[A-Z_]+\}\}", conf), "unsubstituted placeholder")


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_reconnects_after_idle_drop(self):
        """MPD closes idle command connections (connection_timeout); the next
        RPC must transparently reconnect instead of failing with Broken pipe."""
        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            # Simulate the 60s idle timeout: server-side connection is gone.
            await main.S.cmd.close()
            recents = await p.recent_albums(10)  # no MPD call — still fine
            self.assertTrue(recents)
            srv.received.clear()
            await p.play_album("marty::halo", 0)  # MUST reconnect + succeed
            self.assertIn("play 0", srv.received)

    async def test_protocol_ack_does_not_reconnect(self):
        responses = {"listallinfo": LISTALLINFO, "badcmd": b"ACK [5@0] {} unknown command\n"}
        async with FakeMPDServer(responses) as srv:
            await make_plugin(srv)
            conn_before = main.S.cmd
            with self.assertRaises(Exception):
                async with main.S.lock:
                    await main._c("badcmd")
            self.assertIs(main.S.cmd, conn_before, "ACK must not trigger reconnect")


class RootsTests(unittest.IsolatedAsyncioTestCase):
    """Fixed read-only roots via the WaveSafe-owned symlink farm."""

    async def test_refresh_creates_internal_and_prunes_dangling(self):
        import tempfile

        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            with tempfile.TemporaryDirectory() as td:
                main.S.home = os.path.join(td, "home")
                main.S.data_dir = os.path.join(td, "data")
                main.S.music_dir = os.path.join(main.S.data_dir, "library")

                roots = main._refresh_roots()
                farm = main.S.music_dir
                self.assertEqual(roots["internal"], os.path.join(main.S.home, "Music"))
                self.assertTrue(os.path.islink(os.path.join(farm, "internal")))
                self.assertEqual(
                    os.readlink(os.path.join(farm, "internal")),
                    os.path.join(main.S.home, "Music"),
                )

                # A dangling/stale link gets pruned on the next refresh.
                os.symlink("/nonexistent/Music", os.path.join(farm, "sd-gone"))
                main._refresh_roots()
                self.assertFalse(os.path.lexists(os.path.join(farm, "sd-gone")))

    async def test_rescan_library_refreshes_and_updates(self):
        import tempfile

        async with FakeMPDServer({"listallinfo": LISTALLINFO}) as srv:
            p = await make_plugin(srv)
            with tempfile.TemporaryDirectory() as td:
                main.S.home = os.path.join(td, "home")
                main.S.data_dir = os.path.join(td, "data")
                main.S.music_dir = os.path.join(main.S.data_dir, "library")
                srv.received.clear()
                roots = await p.rescan_library()
                self.assertIn("update", srv.received)
                self.assertIn("internal", roots)

    async def test_migration_kills_daemon_on_music_dir_mismatch(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            farm = os.path.join(td, "library")
            os.makedirs(farm)
            responses = {
                "listallinfo": LISTALLINFO,
                "config": f"music_directory: {td}/OLD\nOK\n".encode(),
            }
            async with FakeMPDServer(responses) as srv:
                p = await make_plugin(srv)
                main.S.music_dir = farm
                main.S.socket = "/nonexistent/mpd.sock"
                main.S.host, main.S.port = "127.0.0.1", srv.port
                # Patch respawn: we only verify the kill decision.
                respawned = []

                async def fake_ensure():
                    respawned.append(True)

                orig = main._ensure_mpd_running
                main._ensure_mpd_running = fake_ensure
                try:
                    await main._migrate_music_dir_if_needed()
                finally:
                    main._ensure_mpd_running = orig
                self.assertIn("kill", srv.received)
                self.assertTrue(respawned)

    async def test_migration_noop_when_music_dir_matches(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            farm = os.path.join(td, "library")
            os.makedirs(farm)
            responses = {
                "listallinfo": LISTALLINFO,
                "config": f"music_directory: {farm}\nOK\n".encode(),
            }
            async with FakeMPDServer(responses) as srv:
                p = await make_plugin(srv)
                main.S.music_dir = farm
                await main._migrate_music_dir_if_needed()
                self.assertNotIn("kill", srv.received)


# The exact subsystem list the backend subscribes to (keep in sync with main.py).
IDLE_CMD = "idle player mixer options sticker database update"


class IdleLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_loop_emits_player_event(self):
        responses = {
            "listallinfo": LISTALLINFO,
            "status": STATUS,
            "currentsong": CURRENTSONG,
            "replay_gain_status": RG_STATUS,
            IDLE_CMD: b"changed: player\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            client = MPDClient(None, "127.0.0.1", srv.port)
            await client.connect()
            p = main.Plugin()
            main.S = main._State()
            main.S.cmd = client
            main.S.socket = "/nonexistent/mpd.sock"  # force _connect to use host/port
            main.S.host = "127.0.0.1"
            main.S.port = srv.port
            await main._build_index()

            decky.emitted.clear()
            task = asyncio.create_task(main._idle_loop())
            try:
                for _ in range(50):
                    if decky.emitted:
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(decky.emitted, "idle loop emitted no events")
                event, args = decky.emitted[0]
                self.assertEqual(event, main.PLAYER_EVENT)
                self.assertEqual(args[0]["state"], "play")
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()

    async def test_idle_update_change_starts_scan_monitor(self):
        responses = {
            "listallinfo": LISTALLINFO,
            "status": STATUS,  # no updating_db → monitor exits after one emit
            "stats": b"songs: 3\nOK\n",
            "currentsong": CURRENTSONG,
            "replay_gain_status": RG_STATUS,
            IDLE_CMD: b"changed: update\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            p = await make_plugin(srv)
            decky.emitted.clear()
            task = asyncio.create_task(main._idle_loop())
            try:
                for _ in range(50):
                    if main.S.scan_task is not None:
                        break
                    await asyncio.sleep(0.02)
                self.assertIsNotNone(main.S.scan_task, "update change did not start the monitor")
                await main.S.scan_task
                scans = [args[0] for ev, args in decky.emitted if ev == main.SCAN_EVENT]
                self.assertEqual(scans[-1], {"scanning": False, "songs": 3})
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


class ScanProgressTests(unittest.IsolatedAsyncioTestCase):
    """Scan-progress monitor: 1 Hz status/stats polling STRICTLY while
    `updating_db` is present, then a final scanning=False emit and exit."""

    def setUp(self):
        self._poll = main.SCAN_POLL_SEC
        main.SCAN_POLL_SEC = 0.01

    def tearDown(self):
        main.SCAN_POLL_SEC = self._poll

    @staticmethod
    def _scanning_status(scan_calls: int):
        calls = {"n": 0}

        def handler(_cmd: str) -> bytes:
            calls["n"] += 1
            if calls["n"] <= scan_calls:
                return b"state: stop\nupdating_db: 7\nOK\n"
            return b"state: stop\nOK\n"

        return handler

    async def test_rescan_emits_progress_until_scan_ends(self):
        import tempfile

        responses = {
            "listallinfo": LISTALLINFO,
            "status": self._scanning_status(2),
            "stats": b"songs: 42\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            p = await make_plugin(srv)
            with tempfile.TemporaryDirectory() as td:
                main.S.home = os.path.join(td, "home")
                main.S.data_dir = os.path.join(td, "data")
                main.S.music_dir = os.path.join(main.S.data_dir, "library")
                decky.emitted.clear()
                await p.rescan_library()
                self.assertIsNotNone(main.S.scan_task)
                await main.S.scan_task
        scans = [args[0] for ev, args in decky.emitted if ev == main.SCAN_EVENT]
        self.assertGreaterEqual(len(scans), 3)
        self.assertTrue(all(s["scanning"] for s in scans[:-1]))
        self.assertEqual(scans[-1], {"scanning": False, "songs": 42})

    async def test_start_scan_monitor_does_not_double_spawn(self):
        responses = {
            "listallinfo": LISTALLINFO,
            "status": self._scanning_status(3),
            "stats": b"songs: 1\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            await make_plugin(srv)
            main._start_scan_monitor()
            first = main.S.scan_task
            main._start_scan_monitor()
            self.assertIs(main.S.scan_task, first, "running monitor must not be replaced")
            await first

    async def test_unload_cancels_scan_monitor(self):
        responses = {
            "listallinfo": LISTALLINFO,
            "status": self._scanning_status(10_000),  # effectively never ends
            "stats": b"songs: 1\nOK\n",
        }
        async with FakeMPDServer(responses) as srv:
            p = await make_plugin(srv)
            main._start_scan_monitor()
            await asyncio.sleep(0.05)  # let it start polling
            await p._unload()
            with self.assertRaises(asyncio.CancelledError):
                await main.S.scan_task


if __name__ == "__main__":
    unittest.main()
