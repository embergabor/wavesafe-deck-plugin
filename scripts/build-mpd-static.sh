#!/usr/bin/env bash
# Build a FULLY STATIC musl MPD for the Steam Deck (runs in alpine:latest in CI).
#
# Why static: the repackage-Arch's-closure route was era-pinned to host glibc
# and fought archive keyrings and package splits. A static musl binary has no
# host library dependencies — immune to SteamOS updates, permanently.
#
# Why codecs are built from source here: Alpine's -dev packages ship no .a
# archives for most audio libs (and faad2 has none at all). Each lib below is
# a small, pinned, deterministic build; apk provides only the toolchain plus
# the libs that DO have proper -static packages (sqlite, zlib).
#
# Audio output: a musl binary cannot dlopen the host's glibc PipeWire client,
# so the bundled build uses MPD's `pipe` output → host `pw-cat` (see the
# {{AUDIO_OUTPUT}} rendering in main.py / the Tauri app).
#
# Decoders: flac, mpg123 (mp3), vorbis, opus, faad (aac/m4a), sndfile
# (wav/aiff). ALAC deferred (needs a static ffmpeg subset — follow-up).
# Resampler: soxr (very high) — 44.1 kHz music → the Deck's 48 kHz output
# without the harsh imaging of MPD's basic internal converter.
set -euo pipefail

OUT="${1:?usage: build-mpd-static.sh <outdir>}"
MPD_VERSION="${MPD_VERSION:-0.24.5}"
PREFIX=/usr/local
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig"
# New GCC defaults to C23, where `false`/`bool` are keywords — the venerable
# codec codebases (libsndfile's ALAC, etc.) predate that. Pin C17 for the C
# library builds; MPD itself is C++ and unaffected.
export CFLAGS="-O2 -std=gnu17"
# Alpine's gcc does not search /usr/local by default; faad is found via
# cc.find_library (no pkg-config in MPD's meson), so the paths must be explicit.
export LIBRARY_PATH="$PREFIX/lib"
export CPATH="$PREFIX/include"
JOBS="$(nproc)"

apk add --no-cache >/dev/null \
  build-base meson ninja-build cmake autoconf automake libtool pkgconf \
  linux-headers curl xz bzip2 gperf \
  boost-dev sqlite-dev sqlite-static zlib-dev zlib-static

fetch() { # fetch <url> -> extracted dir on stdout
  local url="$1" tarball dir
  tarball="/tmp/$(basename "$url")"
  curl -fsSL -o "$tarball" "$url"
  dir="/tmp/src-$(basename "$tarball" | sed 's/\.tar\..*//')"
  mkdir -p "$dir"
  tar -xf "$tarball" -C "$dir" --strip-components=1
  echo "$dir"
}

build_autotools() { # build_autotools <url> [extra configure flags...]
  local url="$1"; shift
  local dir; dir="$(fetch "$url")"
  ( cd "$dir" && ./configure --prefix="$PREFIX" --enable-static --disable-shared "$@" >/dev/null \
    && make -j"$JOBS" >/dev/null && make install >/dev/null )
  echo "built: $(basename "$url")"
}

build_cmake() { # build_cmake <url> [extra cmake flags...]
  local url="$1"; shift
  local dir; dir="$(fetch "$url")"
  ( cd "$dir" && cmake -B b -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
      -DBUILD_SHARED_LIBS=OFF -DCMAKE_C_FLAGS="$CFLAGS" \
      -DCMAKE_POLICY_VERSION_MINIMUM=3.5 "$@" >/dev/null \
    && cmake --build b -j"$JOBS" >/dev/null && cmake --install b >/dev/null )
  echo "built: $(basename "$url")"
}

build_autotools "https://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.gz"
build_autotools "https://downloads.xiph.org/releases/vorbis/libvorbis-1.3.7.tar.gz"
build_autotools "https://downloads.xiph.org/releases/flac/flac-1.4.3.tar.xz" \
  --disable-programs --disable-cpplibs --disable-doxygen-docs
build_autotools "https://downloads.xiph.org/releases/opus/opus-1.4.tar.gz" \
  --disable-doc --disable-extra-programs
build_autotools "https://www.mpg123.de/download/mpg123-1.32.6.tar.bz2" \
  --enable-int-quality --disable-components --enable-libmpg123
build_cmake "https://github.com/knik0/faad2/archive/refs/tags/2.11.1.tar.gz"
build_autotools "https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz" \
  --disable-external-libs --disable-mpeg --disable-full-suite
build_cmake "https://codeberg.org/tenacityteam/libid3tag/archive/0.16.3.tar.gz"
# libsoxr — high-quality resampler. No external deps; OpenMP off so there's no
# openmp runtime to statically link. (If SourceForge is flaky, a GitHub soxr
# mirror tarball works the same.) Installs soxr.pc → MPD's meson finds it.
build_cmake "https://downloads.sourceforge.net/project/soxr/soxr-0.1.3-Source.tar.xz" \
  -DWITH_OPENMP=OFF -DBUILD_TESTS=OFF -DBUILD_EXAMPLES=OFF -DWITH_LSR_BINDINGS=OFF

curl -fsSL -o /tmp/mpd.tar.gz \
  "https://github.com/MusicPlayerDaemon/MPD/archive/refs/tags/v${MPD_VERSION}.tar.gz"
mkdir -p /tmp/mpd-src
tar -xzf /tmp/mpd.tar.gz -C /tmp/mpd-src --strip-components=1
cd /tmp/mpd-src

# auto_features=disabled → ONLY what we explicitly enable gets linked in.
LDFLAGS="-static -s -L$PREFIX/lib" meson setup build \
  --buildtype=release \
  -Ddefault_library=static -Dprefer_static=true \
  -Dauto_features=disabled \
  --force-fallback-for=fmt \
  -Ddaemon=true \
  -Ddatabase=true \
  -Dsqlite=enabled \
  -Dflac=enabled \
  -Dvorbis=enabled \
  -Dopus=enabled \
  -Dmpg123=enabled \
  -Dfaad=enabled \
  -Dsndfile=enabled \
  -Did3tag=enabled \
  -Dsoxr=enabled \
  -Dzlib=enabled \
  -Dpipe=true \
  -Dfifo=false \
  -Drecorder=false \
  -Dhttpd=false \
  -Dtest=false >/tmp/meson-setup.log 2>&1 || { tail -40 /tmp/meson-setup.log; exit 1; }

ninja -C build >/dev/null

mkdir -p "$OUT/bin"
cp build/mpd "$OUT/bin/mpd"
chmod +x "$OUT/bin/mpd"

echo "--- verification ---"
file "$OUT/bin/mpd"
file "$OUT/bin/mpd" | grep -q "statically linked" || {
  echo "ERROR: binary is not statically linked"; exit 1; }
du -h "$OUT/bin/mpd"
"$OUT/bin/mpd" --version > /tmp/v.txt 2>&1 || true
head -3 /tmp/v.txt
grep -qi "flac" /tmp/v.txt || { echo "ERROR: flac decoder missing"; exit 1; }
grep -qi "soxr" /tmp/v.txt || { echo "ERROR: soxr resampler missing"; exit 1; }
