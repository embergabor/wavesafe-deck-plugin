// Typed wrappers over the Python backend. Each callable name MUST match a method
// on the `Plugin` class in main.py.
import { callable } from "@decky/api";
import type { Album, PlayerStatus, ReplayGainMode } from "./core";

export const PLAYER_EVENT = "wavesafe_player";
/** Emitted ~1/sec while a library scan is running (and once when it ends). */
export const SCAN_EVENT = "wavesafe_scan";
export interface ScanProgress {
  scanning: boolean;
  songs: number;
}

export const getStatus = callable<[], PlayerStatus>("status");
export const play = callable<[], void>("play");
export const pause = callable<[], void>("pause");
export const togglePlayPause = callable<[], void>("toggle_play_pause");
export const next = callable<[], void>("next");
export const previous = callable<[], void>("previous");
export const seek = callable<[number], void>("seek");
export const toggleShuffle = callable<[], void>("toggle_shuffle");
export const setReplayGain = callable<[ReplayGainMode], void>("set_replay_gain");
/** Music-only volume (MPD software mixer) — balance against game audio. */
export const setVolume = callable<[number], void>("set_volume");

export const playAlbum = callable<[string, number], void>("play_album");
export const recentAlbums = callable<[number], Album[]>("recent_albums");
export const favoriteAlbums = callable<[], Album[]>("favorite_albums");
export const isFavorite = callable<[string], boolean>("is_favorite");
/** Returns the new favorite state (true = now favorited). */
export const toggleFavorite = callable<[string], boolean>("toggle_favorite");
export const coverArt = callable<[string], string | null>("cover_art");
/** Album thumbnail (first track's embedded art) as a base64 data URL. */
export const albumCover = callable<[string], string | null>("album_cover");

export interface ExternalRoot {
  name: string;
  path: string;
  present: boolean;
}
export interface LibraryRoots {
  internal: string;
  externals: ExternalRoot[];
}
/** Fixed READ-ONLY library roots: internal ~/Music + <card>/Music per device. */
export const libraryRoots = callable<[], LibraryRoots>("library_roots");
/** Re-detect roots (SD inserted/removed) and rescan the library. */
export const rescanLibrary = callable<[], LibraryRoots>("rescan_library");

// library browse (text-only drill-down)
export const allAlbums = callable<[], Album[]>("all_albums");
/** Replace the queue with a set of albums (a session); shuffle = MPD random over the whole set. */
export const playAlbums = callable<[string[], boolean], void>("play_albums");
/** Append an album to the current session without interrupting playback. */
export const enqueueAlbum = callable<[string], void>("enqueue_album");
