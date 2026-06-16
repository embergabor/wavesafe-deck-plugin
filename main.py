"""WaveSafe QAM plugin — Decky backend.

Thin in-game control surface: manages a bundled MPD daemon and exposes a small
RPC surface to the React frontend (transport + now-playing + pick-an-album).

Key design points (see docs/PLAN.md):
  - ZERO polling: a background task blocks on MPD `idle` and emits a player-state
    event only when something changes; the frontend interpolates the seek bar.
    Sole sanctioned exception: while a library scan runs, a temporary task polls
    status/stats at 1 Hz to report progress, then exits with the scan.
  - Favorites + custom tags via MPD stickers; gapless + ReplayGain via MPD.
  - Everything lives under /home so it survives SteamOS updates.

Structure note: Decky's sandboxed loader may invoke `Plugin` methods UNBOUND
(passing the class itself as `self`), so instance state is unreliable. All real
state lives in the module-level `S`, all logic in module functions; the Plugin
class is a thin delegation layer that never touches `self`.
"""
from __future__ import annotations

import asyncio
import base64
import os
import signal
import sys
from typing import Any, Optional

import decky  # provided by Decky Loader at runtime

# Decky's sandboxed loader imports main.py without putting the plugin dir on
# sys.path — sibling modules (mpd_client.py) need it added explicitly.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from mpd_client import MPDClient, MPDError, mpd_quote  # noqa: E402

FAV_STICKER = "wavesafe_fav"
PLAYER_EVENT = "wavesafe_player"
SCAN_EVENT = "wavesafe_scan"
SCAN_POLL_SEC = 1.0  # scan-progress poll interval (tests shrink it)


class _State:
    """All backend state (module-level singleton — see structure note above)."""

    def __init__(self) -> None:
        self.home = ""
        self.config_dir = ""
        self.data_dir = ""
        self.music_dir = ""
        self.socket = ""
        self.host = "127.0.0.1"
        self.port = 6600
        self.plugin_dir = _PLUGIN_DIR
        self.lock: asyncio.Lock = asyncio.Lock()
        self.cmd: Optional[MPDClient] = None
        self.idle_task: Optional[asyncio.Task] = None
        self.scan_task: Optional[asyncio.Task] = None
        self.shutting_down = False  # set in _unload so the idle loop won't resurrect
        self.albums: dict[str, _AlbumEntry] = {}
        self.uri_to_album: dict[str, str] = {}


def _norm(s: str) -> str:
    """Match mpd-core makeAlbumKey: lowercase, keep alphanumerics only."""
    return "".join(c for c in s.lower() if c.isalnum())


def album_key(album_artist: str, title: str) -> str:
    return f"{_norm(album_artist)}::{_norm(title)}"


def _int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        # MPD Track can be "3/12" — take the leading number.
        return int(v.split("/", 1)[0])
    except (ValueError, AttributeError):
        return None


def _float(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def track_from_song(song: dict[str, str]) -> dict[str, Any]:
    """Build a contract Track dict from an MPD song record."""
    return {
        "uri": song.get("file", ""),
        "title": song.get("Title") or os.path.basename(song.get("file", "")),
        "artist": song.get("Artist"),
        "album": song.get("Album"),
        "albumArtist": song.get("AlbumArtist") or song.get("Artist"),
        "genre": song.get("Genre"),
        "date": song.get("Date"),
        "trackNo": _int(song.get("Track")),
        "discNo": _int(song.get("Disc")),
        "durationSec": _float(song.get("duration") or song.get("Time")),
    }


class _AlbumEntry:
    __slots__ = ("key", "title", "album_artist", "genre", "year", "uris", "last_modified")

    def __init__(self, key: str, title: str, album_artist: str):
        self.key = key
        self.title = title
        self.album_artist = album_artist
        self.genre: Optional[str] = None
        self.year: Optional[int] = None
        self.uris: list[str] = []
        self.last_modified: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "albumArtist": self.album_artist,
            "year": self.year,
            "genre": self.genre,
        }


S = _State()


# ---- MPD bring-up ------------------------------------------------------------
async def _connect() -> MPDClient:
    use_socket = os.path.exists(S.socket)
    client = MPDClient(S.socket if use_socket else None, S.host, S.port)
    await client.connect()
    return client


def _resolve_mpd_bin() -> Optional[str]:
    """Bundled binary first (production layout), then env override, then PATH /
    common locations (dev/testing on a Deck where mpd was pacman-installed)."""
    import shutil

    bundled = os.path.join(S.plugin_dir, "bin", "mpd")
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    env = os.environ.get("WAVESAFE_MPD_BIN")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("mpd")
    if found:
        return found
    for cand in ("/usr/bin/mpd", "/usr/local/bin/mpd", "/opt/homebrew/bin/mpd"):
        if os.path.isfile(cand):
            return cand
    return None


# Native client output — for a system/dev mpd built against the host's libs.
PIPEWIRE_OUTPUT = """\
audio_output {
    type        "pipewire"
    name        "WaveSafe PipeWire"
    mixer_type  "software"
}"""

# Pipe output — for the bundled STATIC musl mpd, which cannot dlopen the
# host's glibc PipeWire client. Raw PCM goes to the host's pw-cat process.
#
# 32-bit FLOAT at the Deck's native 48 kHz: ReplayGain, the software volume
# mixer, and the soxr resample all run in float, so nothing is quantized to an
# integer until PipeWire converts to the device (with dither). 16-bit output
# here truncated every gain/volume step and made high frequencies harsh.
PIPE_OUTPUT = """\
audio_output {
    type        "pipe"
    name        "WaveSafe PipeWire (pipe)"
    command     "pw-cat -p --rate 48000 --channels 2 --format f32 -"
    format      "48000:f:2"
    mixer_type  "software"
}"""


def _is_bundled(mpd_bin: Optional[str]) -> bool:
    """The bundled binary is the static musl build → needs the pipe output."""
    return bool(mpd_bin) and mpd_bin.startswith(S.plugin_dir + os.sep)


def _render_config(dest: str, mpd_bin: Optional[str] = None) -> None:
    tmpl_path = os.path.join(S.plugin_dir, "daemon", "mpd.conf.tmpl")
    try:
        with open(tmpl_path, "r", encoding="utf-8") as f:
            tmpl = f.read()
    except OSError:
        tmpl = _FALLBACK_CONF
    output = PIPE_OUTPUT if _is_bundled(mpd_bin) else PIPEWIRE_OUTPUT
    rendered = (
        tmpl.replace("{{MUSIC_DIR}}", S.music_dir)
        .replace("{{DATA_DIR}}", S.data_dir)
        .replace("{{SOCKET}}", S.socket)
        .replace("{{AUDIO_OUTPUT}}", output)
    )
    with open(dest, "w", encoding="utf-8") as f:
        f.write(rendered)


async def _ensure_mpd_running() -> None:
    try:
        probe = await _connect()
        await probe.close()
        return
    except Exception:
        pass

    os.makedirs(S.config_dir, exist_ok=True)
    os.makedirs(S.data_dir, exist_ok=True)
    os.makedirs(os.path.join(S.data_dir, "playlists"), exist_ok=True)
    os.makedirs(S.music_dir, exist_ok=True)

    mpd_bin = _resolve_mpd_bin()
    if mpd_bin is None:
        raise MPDError(
            "no mpd binary found (bundled bin/mpd missing and none on PATH) — "
            "see DECK-TESTING.md for how to provide one"
        )
    conf_path = os.path.join(S.config_dir, "mpd.conf")
    _render_config(conf_path, mpd_bin)
    decky.logger.info("Starting MPD: %s", mpd_bin)
    # MPD daemonizes by default, so it survives plugin reloads.
    #
    # Decky runs this backend as ROOT: mpd must NOT run as root, and the
    # pipewire output needs the *user's* session (XDG_RUNTIME_DIR). Demote
    # the spawn to the real user with a matching environment.
    spawn_kwargs: dict = {}
    env = dict(os.environ)
    # Decky Loader is a PyInstaller bundle: it exports LD_LIBRARY_PATH pointing
    # at its own bundled libs (/tmp/_MEI…), whose old libssl breaks system
    # binaries ("OPENSSL_3.2.0 not found" from libcurl). Restore the original
    # value (PyInstaller saves it) or drop it entirely.
    env.pop("LD_LIBRARY_PATH", None)
    _orig_llp = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if _orig_llp:
        env["LD_LIBRARY_PATH"] = _orig_llp
    import pwd

    user = getattr(decky, "DECKY_USER", "deck")
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        uid = os.getuid()
    if os.geteuid() == 0:
        # Root backend (plugin.json "root" flag): demote the daemon to the user.
        spawn_kwargs["user"] = user
        env["HOME"] = S.home
    # plugin_loader is a SYSTEM service — its env (inherited by us and our
    # children) has no session vars. mpd's pipewire output needs the user's
    # XDG_RUNTIME_DIR to find /run/user/<uid>/pipewire-0 ("Host is down" without).
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    spawn_log = os.path.join(S.data_dir, "mpd-spawn.log")
    with open(spawn_log, "ab") as errf:
        proc = await asyncio.create_subprocess_exec(
            mpd_bin,
            conf_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=errf,
            env=env,
            **spawn_kwargs,
        )
        await proc.wait()
    decky.logger.info("mpd spawn exited rc=%s (stderr -> %s)", proc.returncode, spawn_log)

    for _ in range(50):
        if os.path.exists(S.socket):
            try:
                probe = await _connect()
                await probe.close()
                return
            except Exception:
                pass
        await asyncio.sleep(0.1)
    raise MPDError("MPD did not come up within 5s")


async def _stop_mpd() -> None:
    """Stop the bundled daemon when the plugin goes away (unload/uninstall) so
    audio can't orphan with no UI left to stop it.

    SIGTERM via the pid file is the reliable, NON-BLOCKING path (mpd saves its
    state, then exits). We deliberately do NOT route MPD's `kill` command through
    _cmd_run: `kill` drops the connection by design, which _cmd_run treats as a
    dead socket and RETRIES by reconnecting to the dying daemon — that blocked
    _unload past Decky's 5s SIGKILL timeout and orphaned mpd."""
    try:
        with open(os.path.join(S.data_dir, "mpd.pid")) as f:
            os.kill(int(f.read().strip()), signal.SIGTERM)
        return
    except Exception:
        pass
    # Fallback only if the pid file is missing: a one-shot socket kill, no retry.
    if S.cmd is not None and S.cmd.connected:
        try:
            await S.cmd.command("kill")
        except Exception:
            pass


# ---- scan progress monitor ------------------------------------------------------
async def _scan_progress_loop() -> None:
    """Report library-scan progress: while `updating_db` is in status, emit the
    growing song count at 1 Hz (MPD has no x/y progress). Self-terminating —
    the documented exception to the zero-polling rule."""
    try:
        while True:
            async with S.lock:
                st = MPDClient.as_dict(await _c("status"))
                stats = MPDClient.as_dict(await _c("stats"))
            scanning = "updating_db" in st
            await decky.emit(
                SCAN_EVENT,
                {"scanning": scanning, "songs": _int(stats.get("songs")) or 0},
            )
            if not scanning:
                return
            await asyncio.sleep(SCAN_POLL_SEC)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        decky.logger.warning("scan monitor error: %s", e)


def _start_scan_monitor() -> None:
    if S.scan_task is None or S.scan_task.done():
        S.scan_task = asyncio.create_task(_scan_progress_loop())


# ---- idle event loop (zero polling) -------------------------------------------
async def _idle_loop() -> None:
    """Own a SECOND connection dedicated to `idle` (it blocks). On any player/
    mixer/options/sticker/database change, emit fresh state to the UI."""
    idle_conn: Optional[MPDClient] = None
    try:
        idle_conn = await _connect()
        while True:
            changed = await idle_conn.idle(
                "player", "mixer", "options", "sticker", "database", "update"
            )
            if "database" in changed:
                await _build_index()
            if "update" in changed:
                # A scan started (or ended — the monitor's final emit covers that).
                _start_scan_monitor()
            status = await _status()
            await decky.emit(PLAYER_EVENT, status)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # connection dropped (MPD restart) — back off & retry
        # …UNLESS we're shutting down: stopping mpd drops this connection on
        # purpose, and resurrecting here would reconnect-storm a dead daemon and
        # hang _unload past Decky's 5s SIGKILL — which orphans mpd.
        if S.shutting_down:
            return
        decky.logger.warning("idle loop error: %s; retrying", e)
        await asyncio.sleep(1.0)
        if idle_conn is not None:
            await idle_conn.close()
        S.idle_task = asyncio.create_task(_idle_loop())
    finally:
        if idle_conn is not None and idle_conn.connected:
            await idle_conn.close()


# ---- resilient command channel ---------------------------------------------
def _is_conn_error(e: BaseException) -> bool:
    """Dead-socket errors that warrant a reconnect — NOT protocol ACK errors.
    MPD closes idle client connections (connection_timeout, default 60s), and
    this backend is zero-polling by design, so the command connection routinely
    outlives its welcome. The `idle` connection is immune (that's the idiom)."""
    if isinstance(e, (OSError, EOFError, asyncio.IncompleteReadError)):
        return True
    return isinstance(e, MPDError) and "connection closed" in str(e)


async def _c(command: str) -> list[tuple[str, str]]:
    """Run a command on S.cmd (CALLER HOLDS S.lock), reconnecting once if the
    connection died while idle."""
    for attempt in (0, 1):
        try:
            if S.cmd is None or not S.cmd.connected:
                S.cmd = await _connect()
            return await S.cmd.command(command)
        except Exception as e:
            if attempt or not _is_conn_error(e):
                raise
            try:
                if S.cmd is not None:
                    await S.cmd.close()
            except Exception:
                pass
            S.cmd = None
    raise MPDError("unreachable")


async def _cb(command: str):
    """Binary variant of _c (CALLER HOLDS S.lock)."""
    for attempt in (0, 1):
        try:
            if S.cmd is None or not S.cmd.connected:
                S.cmd = await _connect()
            return await S.cmd.command_binary(command)
        except Exception as e:
            if attempt or not _is_conn_error(e):
                raise
            try:
                if S.cmd is not None:
                    await S.cmd.close()
            except Exception:
                pass
            S.cmd = None
    raise MPDError("unreachable")


# ---- library index -------------------------------------------------------------
async def _build_index() -> None:
    async with S.lock:
        pairs = await _c("listallinfo")
    songs = MPDClient.split_objects(pairs, "file")
    albums: dict[str, _AlbumEntry] = {}
    uri_to_album: dict[str, str] = {}
    for song in songs:
        if "file" not in song:
            continue
        artist = song.get("AlbumArtist") or song.get("Artist") or "Unknown Artist"
        title = song.get("Album") or "Unknown Album"
        key = album_key(artist, title)
        entry = albums.get(key)
        if entry is None:
            entry = _AlbumEntry(key, title, artist)
            albums[key] = entry
        if entry.genre is None and song.get("Genre"):
            entry.genre = song["Genre"]
        if entry.year is None and song.get("Date"):
            entry.year = _int(song["Date"])
        lm = song.get("Last-Modified", "")
        if lm > entry.last_modified:
            entry.last_modified = lm
        entry.uris.append((song.get("Disc"), song.get("Track"), song["file"]))  # type: ignore

    # Sort each album's tracks by (disc, track) for gapless ordering; keep only uris.
    for entry in albums.values():
        entry.uris.sort(key=lambda t: ((_int(t[0]) or 1), (_int(t[1]) or 0)))  # type: ignore
        entry.uris = [u for *_rest, u in entry.uris]  # type: ignore
        if entry.uris:
            uri_to_album[entry.uris[0]] = entry.key
    S.albums = albums
    S.uri_to_album = uri_to_album


# ---- transport -----------------------------------------------------------------
async def _cmd_run(command: str) -> list[tuple[str, str]]:
    async with S.lock:
        return await _c(command)


async def _status() -> dict[str, Any]:
    async with S.lock:
        st = MPDClient.as_dict(await _c("status"))
        cur_pairs = await _c("currentsong")
        # ReplayGain mode is NOT in `status` — it has its own command.
        rg = MPDClient.as_dict(await _c("replay_gain_status"))
    cur = MPDClient.as_dict(cur_pairs)
    current = track_from_song(cur) if cur.get("file") else None
    return {
        "state": st.get("state", "stop"),
        "songPos": _int(st.get("song")),
        "current": current,
        "elapsedSec": _float(st.get("elapsed")) or 0.0,
        "durationSec": _float(st.get("duration")),
        "random": st.get("random") == "1",
        "repeat": st.get("repeat") == "1",
        "volume": _int(st.get("volume")),
        "replayGainMode": rg.get("replay_gain_mode", "off"),
    }


async def _play_album(album_key_value: str, start_index: int = 0) -> None:
    """Play one album front-to-back: always album order, so a leftover shuffle
    from an earlier session must not scramble it."""
    entry = S.albums.get(album_key_value)
    if entry is None or not entry.uris:
        raise MPDError(f"unknown album: {album_key_value}")
    async with S.lock:
        await _c("clear")
        for uri in entry.uris:
            await _c(f"add {mpd_quote(uri)}")
        await _c("random 0")
        await _c(f"play {max(0, start_index)}")


async def _play_albums(album_keys: list, shuffle: bool) -> None:
    """Replace the queue with a whole set of albums (a listening session).
    shuffle=True → MPD random over the entire set; False → album order
    (gapless within each album). Mirrors the desktop Rust play_albums."""
    uris: list[str] = []
    for key in album_keys:
        entry = S.albums.get(key)
        if entry is not None:
            uris.extend(entry.uris)
    if not uris:
        raise MPDError("no tracks in selection")
    async with S.lock:
        await _c("clear")
        for uri in uris:
            await _c(f"add {mpd_quote(uri)}")
        await _c("random 1" if shuffle else "random 0")
        await _c("play 0")


async def _enqueue_album(album_key_value: str) -> None:
    """Append an album to the queue without disturbing what's playing."""
    entry = S.albums.get(album_key_value)
    if entry is None or not entry.uris:
        raise MPDError(f"unknown album: {album_key_value}")
    async with S.lock:
        for uri in entry.uris:
            await _c(f"add {mpd_quote(uri)}")


def _album_first_uri(key: str) -> Optional[str]:
    entry = S.albums.get(key)
    return entry.uris[0] if entry and entry.uris else None


async def _is_favorite(album_key_value: str) -> bool:
    uri = _album_first_uri(album_key_value)
    if uri is None:
        return False
    try:
        await _cmd_run(f"sticker get song {mpd_quote(uri)} {mpd_quote(FAV_STICKER)}")
        return True
    except MPDError:
        return False


async def _cover_art(uri: str) -> Optional[str]:
    async with S.lock:
        chunks = bytearray()
        offset = 0
        mime = "image/jpeg"
        while True:
            try:
                pairs, blob = await _cb(f"readpicture {mpd_quote(uri)} {offset}")
            except MPDError:
                return None
            meta = MPDClient.as_dict(pairs)
            if blob is None:
                break
            chunks.extend(blob)
            mime = meta.get("type", mime)
            size = int(meta.get("size", "0"))
            offset = len(chunks)
            if offset >= size or not blob:
                break
    if not chunks:
        return None
    return f"data:{mime};base64," + base64.b64encode(bytes(chunks)).decode("ascii")


# ---- library roots (fixed, READ-ONLY sources via a WaveSafe-owned symlink farm)
def _refresh_roots() -> dict:
    """music_directory is a symlink farm WE own (S.music_dir):
        internal   -> <home>/Music                  (always)
        sd-<name>  -> /run/media/.../Music          (per mounted card/drive)
    The real music locations are never written to. Dangling links are pruned;
    MPD prunes vanished files from the DB on the next update."""
    farm = S.music_dir
    os.makedirs(farm, exist_ok=True)
    internal = os.path.join(S.home, "Music")
    os.makedirs(internal, exist_ok=True)

    desired = {"internal": internal}
    base = "/run/media"
    mounts: list = []
    try:
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if not os.path.isdir(p):
                continue
            if name in ("deck", "root"):
                mounts += [os.path.join(p, sub) for sub in os.listdir(p)
                           if os.path.isdir(os.path.join(p, sub))]
            else:
                mounts.append(p)
    except OSError:
        pass
    for mount in sorted(set(mounts)):
        music = os.path.join(mount, "Music")
        if os.path.isdir(music):
            slug = "sd-" + _norm(os.path.basename(mount))[:24]
            desired[slug] = music

    # Sync the farm to `desired` (remove stale/dangling, add missing).
    for entry in os.listdir(farm):
        link = os.path.join(farm, entry)
        if not os.path.islink(link):
            continue
        if entry not in desired or os.readlink(link) != desired[entry]:
            os.unlink(link)
    for name, target in desired.items():
        link = os.path.join(farm, name)
        if not os.path.islink(link):
            os.symlink(target, link)

    externals = [
        {"name": n, "path": t, "present": os.path.isdir(t)}
        for n, t in desired.items() if n != "internal"
    ]
    return {"internal": internal, "externals": externals}


async def _migrate_music_dir_if_needed() -> None:
    """A daemon started by an older plugin uses the old music_directory. The
    `config` command (local clients only) reports it; on mismatch, kill the
    daemon and respawn onto the symlink farm."""
    try:
        cfg = MPDClient.as_dict(await _cmd_run("config"))
    except MPDError:
        return  # non-local connection — nothing we can do
    current = cfg.get("music_directory", "")
    if current and os.path.realpath(current) != os.path.realpath(S.music_dir):
        decky.logger.info("music_directory migration: %s -> %s", current, S.music_dir)
        try:
            await _cmd_run("kill")
        except Exception:
            pass  # connection drops as the daemon dies — expected
        if S.cmd is not None:
            try:
                await S.cmd.close()
            except Exception:
                pass
            S.cmd = None
        await asyncio.sleep(0.5)
        await _ensure_mpd_running()


# ---- Decky surface (thin delegation; never relies on bound `self`) -------------
class Plugin:
    async def _main(self):
        # Decky runs plugin backends as root — $HOME would be /root. Decky
        # exposes the real user's home; fall back to $HOME for dev/test runs.
        S.home = getattr(decky, "DECKY_USER_HOME", None) or os.environ.get("HOME", "/home/deck")
        S.config_dir = os.path.join(S.home, ".config", "wavesafe")
        S.data_dir = os.path.join(S.home, ".local", "share", "wavesafe")
        S.music_dir = os.path.join(S.data_dir, "library")  # the symlink farm
        S.socket = os.path.join(S.config_dir, "mpd.sock")
        S.plugin_dir = getattr(decky, "DECKY_PLUGIN_DIR", _PLUGIN_DIR)
        S.lock = asyncio.Lock()
        S.shutting_down = False

        _refresh_roots()
        await _ensure_mpd_running()
        S.cmd = await _connect()
        await _migrate_music_dir_if_needed()
        await _cmd_run("update")  # cheap when unchanged; picks up SD changes
        await _build_index()
        S.idle_task = asyncio.create_task(_idle_loop())
        decky.logger.info("WaveSafe plugin started (MPD %s)", S.cmd.version)

    async def _unload(self):
        # Set the flag BEFORE anything drops a connection, so the idle loop
        # returns instead of resurrecting (a reconnect storm here hangs _unload
        # past Decky's 5s SIGKILL — which is what orphaned mpd). Then cancel the
        # tasks and stop the daemon. Keep this fast: no awaiting cancelled tasks.
        S.shutting_down = True
        if S.idle_task is not None:
            S.idle_task.cancel()
            S.idle_task = None
        if S.scan_task is not None:
            S.scan_task.cancel()
            S.scan_task = None
        await _stop_mpd()
        if S.cmd is not None:
            try:
                await S.cmd.close()
            except Exception:
                pass
            S.cmd = None
        decky.logger.info("WaveSafe plugin unloaded (MPD stopped)")

    async def _uninstall(self):
        # Removing the plugin removes the UI — make sure the daemon goes too.
        await _stop_mpd()
        decky.logger.info("WaveSafe plugin uninstalled (MPD stopped)")

    # transport
    async def status(self):
        return await _status()

    async def play(self):
        await _cmd_run("play")

    async def pause(self):
        await _cmd_run("pause 1")

    async def toggle_play_pause(self):
        await _cmd_run("pause")  # bare `pause` toggles

    async def next(self):
        await _cmd_run("next")

    async def previous(self):
        await _cmd_run("previous")

    async def seek(self, seconds: float):
        await _cmd_run(f"seekcur {seconds:.3f}")

    async def toggle_shuffle(self):
        st = MPDClient.as_dict(await _cmd_run("status"))
        await _cmd_run("random " + ("0" if st.get("random") == "1" else "1"))

    async def set_replay_gain(self, mode: str):
        if mode not in ("off", "track", "album", "auto"):
            raise ValueError(f"bad replay gain mode: {mode}")
        await _cmd_run(f"replay_gain_mode {mode}")

    async def set_volume(self, level: int):
        """Music volume via MPD's software mixer — balances against game audio
        without touching the system volume (which would move the game too)."""
        await _cmd_run(f"setvol {max(0, min(100, int(level)))}")

    # selection
    async def play_album(self, album_key_value: str, start_index: int = 0):
        await _play_album(album_key_value, start_index)

    async def play_albums(self, album_keys: list, shuffle: bool = False):
        await _play_albums(album_keys, shuffle)

    async def enqueue_album(self, album_key_value: str):
        await _enqueue_album(album_key_value)

    async def all_albums(self):
        """Full album list for client-side browse/grouping (text-only rows)."""
        entries = sorted(
            S.albums.values(),
            key=lambda e: (e.album_artist.lower(), e.title.lower()),
        )
        return [e.to_dict() for e in entries]

    # storage (fixed read-only roots)
    async def library_roots(self):
        return _refresh_roots()

    async def rescan_library(self):
        """Refresh root symlinks (SD inserted/removed) and rescan. The database
        idle event refreshes all clients when the scan lands."""
        roots = _refresh_roots()
        await _cmd_run("update")
        _start_scan_monitor()
        return roots

    async def recent_albums(self, limit: int = 12):
        # "Recently added" via max Last-Modified (MPD has no play history).
        entries = sorted(S.albums.values(), key=lambda e: e.last_modified, reverse=True)
        return [e.to_dict() for e in entries[: max(0, limit)]]

    async def favorite_albums(self):
        pairs = await _cmd_run(f'sticker find song "" {mpd_quote(FAV_STICKER)}')
        result = []
        for obj in MPDClient.split_objects(pairs, "file"):
            key = S.uri_to_album.get(obj.get("file", ""))
            if key and key in S.albums:
                result.append(S.albums[key].to_dict())
        return result

    # favorites (stickers)
    async def is_favorite(self, album_key_value: str):
        return await _is_favorite(album_key_value)

    async def toggle_favorite(self, album_key_value: str):
        uri = _album_first_uri(album_key_value)
        if uri is None:
            return False
        if await _is_favorite(album_key_value):
            await _cmd_run(f"sticker delete song {mpd_quote(uri)} {mpd_quote(FAV_STICKER)}")
            return False
        await _cmd_run(f'sticker set song {mpd_quote(uri)} {mpd_quote(FAV_STICKER)} "1"')
        return True

    # cover art — fetched once per track by the frontend then cached.
    async def cover_art(self, uri: str):
        return await _cover_art(uri)

    async def album_cover(self, album_key_value: str):
        """Album thumbnail source: embedded art of the album's first track."""
        uri = _album_first_uri(album_key_value)
        return await _cover_art(uri) if uri else None


# Minimal config used if the bundled template can't be found at runtime.
# NB: mpd's parser requires block braces on their own lines — a one-line
# `audio_output { ... }` is a parse error ("Unknown tokens after '{'").
_FALLBACK_CONF = """\
music_directory "{{MUSIC_DIR}}"
db_file "{{DATA_DIR}}/database"
state_file "{{DATA_DIR}}/state"
state_file_interval "60"
sticker_file "{{DATA_DIR}}/sticker.sql"
pid_file "{{DATA_DIR}}/mpd.pid"
log_file "{{DATA_DIR}}/mpd.log"
bind_to_address "{{SOCKET}}"
bind_to_address "127.0.0.1"
port "6600"
auto_update "no"
follow_outside_symlinks "yes"
follow_inside_symlinks "yes"
restore_paused "yes"
zeroconf_enabled "no"
replaygain "auto"
replaygain_limit "yes"
samplerate_converter "soxr very high"
{{AUDIO_OUTPUT}}
decoder {
    plugin "fluidsynth"
    enabled "no"
}
decoder {
    plugin "wildmidi"
    enabled "no"
}
"""
