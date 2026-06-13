# Functional tests — real MPD, real files

Runs the **real backend** (`main.py` + `mpd_client.py`) against a **real MPD
daemon** playing **real tagged FLACs**, on macOS or Linux. No Deck needed; no
Docker needed (a native daemon is closer to the Deck than a container anyway).

```bash
brew install mpd flac        # one-time (macOS); any distro package works too
# from packages/qam-plugin/
python3 -m unittest discover -s tests_functional -p 'test_*.py'
# or from the repo root: npm run test:func
```

The whole module **skips cleanly** if `mpd`/`flac` aren't on PATH.

## How it works

- **`media_gen.py`** — builds a tiny 2-album library: stdlib-generated WAVs
  (sine), a hand-built PNG, encoded by the `flac` CLI with real Vorbis tags
  (incl. the `ost_game` custom Soundtrack genre) and an embedded PICTURE block.
  Track 2 is deliberately encoded to disk before track 1 so indexing must
  reorder by real `TRACKNUMBER` tags.
- **`mpd_harness.py`** — renders the repo's **shipped** `daemon/mpd.conf.tmpl`
  (the template itself is under test) into a tmp dir and boots `mpd --no-daemon`
  on a unix socket. Only test-only change: `pipewire` output → `null` (macOS has
  no PipeWire; `null` plays at realtime so seek/elapsed/advance are real).
- **`test_functional.py`** —
  - daemon boots from the shipped template
  - real tag extraction → index (album keys, genre/year, gapless `(disc,track)` order)
  - `play_album` actually plays; MPD auto-advances through real 0.3s tracks
  - seek + elapsed on a 3s track; shuffle + ReplayGain mode round-trips
  - **favorites (stickers) and the queue survive a daemon restart** (sticker.sql + state_file)
  - `readpicture` returns the embedded art **byte-exact**
  - the `idle` loop emits a player event on a real state change

## Bugs this suite has already caught

1. Real MPD does **not** report ReplayGain mode in `status` — it's a separate
   `replay_gain_status` command (the unit fake had encoded the wrong assumption).
2. Its response key is `replay_gain_mode` (not `replaygain`) — verified against
   MPD 0.24. Fixed in `main.py` **and** the Rust `mpd.rs`, fake updated to match.

Moral: the unit fake is fast and deterministic, but only the functional suite
keeps it honest. When MPD behavior is in doubt, add a probe here first.
