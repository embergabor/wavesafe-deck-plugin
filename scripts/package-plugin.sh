#!/usr/bin/env bash
# Package the WaveSafe Decky plugin into an installable zip.
#
# Layout (what Decky Loader expects in ~/homebrew/plugins/WaveSafe/ and what
# main.py expects relative to DECKY_PLUGIN_DIR):
#   WaveSafe/
#     plugin.json            Decky manifest
#     package.json           version info for Decky
#     LICENSE                MIT (required by the Decky store)
#     THIRD-PARTY-NOTICES.md GPL-2.0 notice for the bundled mpd binary
#     main.py                backend (Plugin class)
#     mpd_client.py          MPD protocol client
#     dist/index.js          built frontend (@decky/ui panel)
#     daemon/mpd.conf.tmpl   config template rendered at first run
#     bin/mpd                bundled static-musl MPD binary
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/out"
STAGE="$OUT/WaveSafe"

if [ ! -f "$ROOT/dist/index.js" ]; then
  echo "dist/index.js missing — run: pnpm run build" >&2
  exit 1
fi

rm -rf "$STAGE" "$OUT/WaveSafe.zip"
mkdir -p "$STAGE/dist" "$STAGE/daemon" "$STAGE/bin"

cp "$ROOT/plugin.json" "$STAGE/"
cp "$ROOT/package.json" "$STAGE/"
cp "$ROOT/LICENSE" "$STAGE/"
cp "$ROOT/THIRD-PARTY-NOTICES.md" "$STAGE/"
cp "$ROOT/main.py" "$STAGE/"
cp "$ROOT/mpd_client.py" "$STAGE/"
cp "$ROOT/dist/index.js" "$STAGE/dist/"
cp "$ROOT/daemon/mpd.conf.tmpl" "$STAGE/daemon/"

if [ -f "$ROOT/bin/mpd" ]; then
  cp "$ROOT/bin/mpd" "$STAGE/bin/mpd"
  chmod +x "$STAGE/bin/mpd"
  echo "bundled: static mpd ($(du -h "$STAGE/bin/mpd" | cut -f1 | tr -d ' '))"
else
  echo "note: no bundled mpd at bin/mpd — plugin will use mpd from PATH on the Deck"
fi

(cd "$OUT" && zip -qr WaveSafe.zip WaveSafe)
echo "packaged: $OUT/WaveSafe.zip"
unzip -l "$OUT/WaveSafe.zip"
