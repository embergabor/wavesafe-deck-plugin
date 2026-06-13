/**
 * Player-state types and the RPC contract both frontends rely on.
 *
 * The raw MPD wire protocol does NOT live here: the QAM plugin's Python backend
 * and the desktop app's Rust backend each talk to MPD natively and return JSON
 * shaped like these types. This file is the single source of truth for that
 * shape, so the two frontends interpret player state identically.
 */
import type { Track } from "./models.js";

export type PlaybackState = "play" | "pause" | "stop";

/** Snapshot of MPD's player status (from `status` + `currentsong`). */
export interface PlayerStatus {
  state: PlaybackState;
  /** Index of the current song in the queue, or null when stopped/empty. */
  songPos: number | null;
  /** The current track, or null. */
  current: Track | null;
  /** Elapsed seconds into the current track at the moment this snapshot was taken. */
  elapsedSec: number;
  /** Total duration of the current track in seconds, if known. */
  durationSec: number | null;
  /** MPD random flag — backs the in-app shuffle toggle. */
  random: boolean;
  repeat: boolean;
  /** 0..100, or null if the output has no software mixer. */
  volume: number | null;
  /** Whether ReplayGain is active (maps to WaveSafe's normalizeLoudness). */
  replayGainMode: ReplayGainMode;
}

export type ReplayGainMode = "off" | "track" | "album" | "auto";

/**
 * Client-side seek interpolation. The QAM plugin must NOT poll MPD per second
 * (battery). Take one {@link PlayerStatus} snapshot plus the wall-clock time it
 * was taken, then compute the live position locally. Mirrors the design note in
 * docs/PLAN.md ("seek bar interpolates client-side from one status read").
 *
 * @param snapshotElapsedSec  status.elapsedSec at capture time
 * @param snapshotAtMs        Date.now() at capture time
 * @param nowMs               current Date.now()
 * @param state               playback state (only advances while playing)
 * @param durationSec         track duration, used as a ceiling
 */
export function interpolateElapsed(
  snapshotElapsedSec: number,
  snapshotAtMs: number,
  nowMs: number,
  state: PlaybackState,
  durationSec: number | null,
): number {
  if (state !== "play") return snapshotElapsedSec;
  const advanced = snapshotElapsedSec + (nowMs - snapshotAtMs) / 1000;
  if (durationSec != null && advanced > durationSec) return durationSec;
  return advanced < 0 ? 0 : advanced;
}

/**
 * The RPC surface both backends implement (Python in Decky, Rust in Tauri). The
 * QAM plugin uses the thin subset; the desktop app uses all of it. Methods are
 * async because they cross the frontend⇄backend boundary.
 */
export interface MpdBackend {
  // --- transport (thin / QAM) ---
  status(): Promise<PlayerStatus>;
  play(): Promise<void>;
  pause(): Promise<void>;
  togglePlayPause(): Promise<void>;
  next(): Promise<void>;
  previous(): Promise<void>;
  /** Seek to an absolute position in the current track. */
  seek(seconds: number): Promise<void>;
  /** Toggle MPD random mode (the shuffle toggle). */
  toggleShuffle(): Promise<void>;
  setReplayGain(mode: ReplayGainMode): Promise<void>;

  // --- selection (thin / QAM quick list + desktop) ---
  /** Replace the queue with an album's tracks (gapless within the album) and play. */
  playAlbum(albumKey: string, startIndex?: number): Promise<void>;
  /** Recently added/played albums for the QAM quick list. */
  recentAlbums(limit: number): Promise<import("./models.js").Album[]>;
  favoriteAlbums(): Promise<import("./models.js").Album[]>;

  // --- library (desktop) ---
  listArtists(): Promise<string[]>;
  albumsByArtist(artist: string): Promise<import("./models.js").Album[]>;
  albumTracks(albumKey: string): Promise<Track[]>;

  // --- favorites (stickers) ---
  isFavorite(albumKey: string): Promise<boolean>;
  toggleFavorite(albumKey: string): Promise<boolean>;

  // --- cover art ---
  /** Base64 (data URL) cover for a track/album; null if none. Fetch once, cache. */
  coverArt(uri: string): Promise<string | null>;
}
