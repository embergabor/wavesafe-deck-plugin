"""Generate small, real, tagged audio files for functional tests.

Pure stdlib for the raw media (WAV via `wave`, PNG via `zlib`+`struct`), then the
`flac` CLI (brew install flac) encodes to FLAC with Vorbis tags and an embedded
PICTURE block — i.e. exactly the kind of files WaveSafe libraries hold.
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import wave
import zlib

FLAC_BIN = shutil.which("flac")


def make_wav(path: str, seconds: float = 0.3, freq: float = 440.0, rate: int = 44100) -> None:
    """Write a small stereo 16-bit sine WAV."""
    n = int(seconds * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(0.2 * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))


def make_png(path: str, size: int = 8, rgb: tuple[int, int, int] = (200, 40, 40)) -> bytes:
    """Write a tiny solid-color PNG; returns its bytes (for byte-exact asserts)."""

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + bytes(rgb) * size  # filter 0 + pixels
    idat = zlib.compress(row * size)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return png


def make_flac(
    wav_path: str,
    flac_path: str,
    *,
    title: str,
    artist: str,
    album: str,
    album_artist: str | None = None,
    genre: str | None = None,
    date: str | None = None,
    track: int | None = None,
    disc: int | None = None,
    picture: str | None = None,
) -> None:
    """Encode a WAV to FLAC with Vorbis tags (requires the `flac` CLI)."""
    assert FLAC_BIN, "flac CLI not installed"
    cmd = [FLAC_BIN, "--silent", "--force", "-o", flac_path]
    tags = {
        "TITLE": title,
        "ARTIST": artist,
        "ALBUM": album,
        "ALBUMARTIST": album_artist,
        "GENRE": genre,
        "DATE": date,
        "TRACKNUMBER": str(track) if track is not None else None,
        "DISCNUMBER": str(disc) if disc is not None else None,
    }
    for k, v in tags.items():
        if v is not None:
            cmd.append(f"--tag={k}={v}")
    if picture is not None:
        cmd.append(f"--picture={picture}")
    cmd.append(wav_path)
    subprocess.run(cmd, check=True, capture_output=True)


def build_test_library(music_dir: str) -> dict:
    """Create a small two-album library (one regular, one ost_game soundtrack).

    Returns a manifest with the embedded PNG bytes and expected album keys.
    """
    os.makedirs(music_dir, exist_ok=True)
    scratch = os.path.join(music_dir, "_scratch")
    os.makedirs(scratch, exist_ok=True)

    wav_short = os.path.join(scratch, "short.wav")
    wav_long = os.path.join(scratch, "long.wav")
    make_wav(wav_short, seconds=0.3)
    make_wav(wav_long, seconds=3.0)
    png_path = os.path.join(scratch, "cover.png")
    png_bytes = make_png(png_path)

    abbey = os.path.join(music_dir, "beatles", "abbey")
    halo = os.path.join(music_dir, "ost", "halo")
    os.makedirs(abbey, exist_ok=True)
    os.makedirs(halo, exist_ok=True)

    # Intentionally encode track 2 "first" on disk — index must reorder by (disc,track).
    make_flac(wav_short, os.path.join(abbey, "two.flac"), title="Two", artist="The Beatles",
              album="Abbey Road", album_artist="The Beatles", genre="Rock", date="1969",
              track=2, disc=1, picture=png_path)
    make_flac(wav_short, os.path.join(abbey, "one.flac"), title="One", artist="The Beatles",
              album="Abbey Road", album_artist="The Beatles", genre="Rock", date="1969",
              track=1, disc=1, picture=png_path)
    make_flac(wav_long, os.path.join(halo, "theme.flac"), title="Halo Theme", artist="Marty",
              album="Halo", album_artist="Marty", genre="ost_game", date="2001", track=1)

    shutil.rmtree(scratch)
    return {
        "png": png_bytes,
        "abbey_key": "thebeatles::abbeyroad",
        "halo_key": "marty::halo",
        "abbey_uris": ["beatles/abbey/one.flac", "beatles/abbey/two.flac"],
    }
