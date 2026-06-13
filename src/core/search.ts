/**
 * Offline library search across artists, albums, and tracks. Ported from
 * WaveSafe's `Model/LibrarySearch.swift`: prefix matches rank above substring
 * matches; each category capped at `limit`; case- and diacritic-insensitive.
 *
 * Pure: operates on already-loaded library data (the backend hands the frontend
 * the album/track lists; search runs client-side).
 */
import type { Album, Track } from "./models.js";

export interface LibrarySearchResults {
  artists: string[];
  albums: Album[];
  tracks: Track[];
}

/** Case- and diacritic-insensitive fold. Mirrors `Library.searchFold`. */
export function searchFold(s: string): string {
  return s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

// 0 = prefix match, 1 = substring match, null = no match.
function rank(value: string, q: string): number | null {
  const f = searchFold(value);
  if (f.startsWith(q)) return 0;
  if (f.includes(q)) return 1;
  return null;
}

function bestRank(values: Array<string | undefined>, q: string): number | null {
  let best: number | null = null;
  for (const v of values) {
    if (v == null) continue;
    const r = rank(v, q);
    if (r !== null && (best === null || r < best)) best = r;
  }
  return best;
}

function byTitle(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "accent" });
}

/**
 * Search `albums`/`tracks` against `query`. Artists are derived from distinct
 * album artists. Ignores any "hide OST" setting — an explicit query finds
 * everything.
 */
export function searchLibrary(
  albums: ReadonlyArray<Album>,
  tracks: ReadonlyArray<Track>,
  query: string,
  limit = 50,
): LibrarySearchResults {
  const q = searchFold(query.trim());
  if (q.length === 0) return { artists: [], albums: [], tracks: [] };

  // Artists (distinct album artists)
  const artistRanks: Array<{ name: string; rank: number }> = [];
  for (const name of new Set(albums.map((a) => a.albumArtist))) {
    const r = rank(name, q);
    if (r !== null) artistRanks.push({ name, rank: r });
  }
  artistRanks.sort((a, b) => (a.rank !== b.rank ? a.rank - b.rank : byTitle(a.name, b.name)));

  // Albums (title or album artist)
  const albumRanks: Array<{ album: Album; rank: number }> = [];
  for (const a of albums) {
    const r = bestRank([a.title, a.albumArtist], q);
    if (r !== null) albumRanks.push({ album: a, rank: r });
  }
  albumRanks.sort((a, b) => (a.rank !== b.rank ? a.rank - b.rank : byTitle(a.album.title, b.album.title)));

  // Tracks (title or track artist)
  const trackRanks: Array<{ track: Track; rank: number }> = [];
  for (const t of tracks) {
    const r = bestRank([t.title, t.artist], q);
    if (r !== null) trackRanks.push({ track: t, rank: r });
  }
  trackRanks.sort((a, b) => (a.rank !== b.rank ? a.rank - b.rank : byTitle(a.track.title, b.track.title)));

  return {
    artists: artistRanks.slice(0, limit).map((x) => x.name),
    albums: albumRanks.slice(0, limit).map((x) => x.album),
    tracks: trackRanks.slice(0, limit).map((x) => x.track),
  };
}
