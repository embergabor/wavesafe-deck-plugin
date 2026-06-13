# QAM plugin tests

Pure-logic tests for the Python backend that run on any machine with `python3`
(no Deck, MPD, or PipeWire needed) — they exercise the real `mpd_client.py` and
`main.py` code against an in-process **fake MPD server** (`fake_mpd.py`).

```bash
# from packages/qam-plugin/
python3 -m unittest discover -s tests -p 'test_*.py'
```

## What's covered

- **`test_mpd_client.py`** — the protocol client: greeting/version, status &
  song parsing, `split_objects`, binary `readpicture` reads, `idle`, `ACK` →
  `MPDError`, and argument quoting.
- **`test_main.py`** — backend logic: album-key normalization, `Track:"n/m"`
  parsing, the library index (incl. `(disc,track)` gapless ordering and
  uri→album mapping), `recent_albums` ordering, the `play_album` command
  sequence, `cover_art` multi-chunk assembly, sticker-backed favorites round-trip,
  `status()` shape, and the **`idle`-driven event loop** emitting a player event.

`stubs/decky.py` stands in for the `decky` module Decky injects at runtime and
records emitted events for assertions.

## What this does NOT cover

The `@decky/ui` React frontend, the Tauri/Rust build, and real MPD/PipeWire
behavior (the in-game "core gate") require a Steam Deck. See `docs/PLAN.md`.

> The idle-loop test already paid for itself: it caught `_connect()` ignoring the
> configured host/port (falling back to `127.0.0.1:6600`).
