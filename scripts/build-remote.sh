#!/usr/bin/env bash
# Rebuild the static musl MPD on the LAN build server (LXD alpine container)
# and refresh the committed binary at bin/mpd.
#
# Use when bumping MPD_VERSION or codec/resampler versions in
# build-mpd-static.sh. The binary is committed to git (small, rare updates);
# this script is its provenance: same script CI would run, same pinned alpine.
#
#   ./scripts/build-remote.sh                 # default server below
#   BUILD_HOST=user@host ./scripts/build-remote.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_HOST="${BUILD_HOST:-embergabor@192.168.0.13}"
SSH=(ssh -i "$HOME/.ssh/id_deck_wavesafe" -o IdentitiesOnly=yes "$BUILD_HOST")
CONTAINER=wavesafe-build
ALPINE=images:alpine/3.22

echo "== ensuring container =="
"${SSH[@]}" "lxc info $CONTAINER >/dev/null 2>&1 || lxc launch -q $ALPINE $CONTAINER; lxc start $CONTAINER 2>/dev/null || true"

echo "== pushing build script =="
scp -i "$HOME/.ssh/id_deck_wavesafe" -o IdentitiesOnly=yes -q \
  "$ROOT/scripts/build-mpd-static.sh" "$BUILD_HOST:/tmp/build-mpd-static.sh"
"${SSH[@]}" "lxc file push -q /tmp/build-mpd-static.sh $CONTAINER/root/build.sh"

echo "== building (watch htop on the server) =="
"${SSH[@]}" "lxc exec $CONTAINER -- sh -c 'apk add --no-cache bash file >/dev/null 2>&1; bash /root/build.sh /root/out' 2>&1 | tail -8"

echo "== pulling binary =="
"${SSH[@]}" "lxc file pull -q $CONTAINER/root/out/bin/mpd /tmp/mpd-static"
scp -i "$HOME/.ssh/id_deck_wavesafe" -o IdentitiesOnly=yes -q \
  "$BUILD_HOST:/tmp/mpd-static" "$ROOT/bin/mpd"
chmod +x "$ROOT/bin/mpd"
file "$ROOT/bin/mpd"
echo "done — review and commit bin/mpd"
