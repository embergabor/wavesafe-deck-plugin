# Third-party notices

WaveSafe for Steam Deck's own code is licensed under the [MIT License](LICENSE).
The packaged plugin **redistributes** the following third-party software, which
keeps its own license. WaveSafe invokes it as a separate process (a daemon it
spawns), not as a linked library.

## Music Player Daemon (MPD)

- **Component:** `bin/mpd` — a statically linked (musl) build of the Music
  Player Daemon, version **0.24.5**.
- **License:** GNU General Public License, version 2 (**GPL-2.0**). MPD is
  distributed under the terms of GPL-2.0; some source files are
  "GPL-2.0-or-later".
- **Upstream source:** <https://github.com/MusicPlayerDaemon/MPD> (release
  `v0.24.5`) — also <https://www.musicpd.org/>.
- **Corresponding source / build:** the exact, unmodified upstream source for
  version 0.24.5 above, built with the flags recorded in
  [`scripts/build-mpd-static.sh`](scripts/build-mpd-static.sh). No MPD source
  was modified; only the build configuration (static musl, decoder selection)
  is ours. A copy of the GPL-2.0 text ships with MPD upstream and is available
  at <https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt>.

As required by GPL-2.0, the corresponding source for the bundled `mpd` binary
is available from the upstream release above; our build configuration is in
this repository.
