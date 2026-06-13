/**
 * Domain models. These are derived from MPD's tag database (the raw-tag source),
 * not persisted by us — MPD owns the library. Mirrors WaveSafe's Track/Album
 * shape closely enough that the UI layer ports cleanly.
 */

/** A single song, built from one MPD song record (`file` + tags). */
export interface Track {
  /** MPD song URI (relative to music_directory). The stable identity. */
  uri: string;
  title: string;
  artist?: string;
  album?: string;
  albumArtist?: string;
  genre?: string;
  /** Raw date/year tag as MPD returns it (e.g. "1999", "1999-10-04"). */
  date?: string;
  trackNo?: number;
  discNo?: number;
  durationSec?: number;
}

/** An album, aggregated from its tracks. Cover art is fetched separately from MPD. */
export interface Album {
  /** Dedup/identity key — see {@link makeAlbumKey}. */
  key: string;
  title: string;
  albumArtist: string;
  year?: number;
  genre?: string;
}

/**
 * Stable album identity from album-artist + title, normalized to collapse
 * case/spacing/punctuation differences (so "The Beatles — Abbey Road" and
 * "the beatles - abbey road" coincide). Analogous to WaveSafe's `Album.key`.
 */
export function makeAlbumKey(albumArtist: string, title: string): string {
  const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  return `${norm(albumArtist)}::${norm(title)}`;
}

/** Best-effort album artist: prefer AlbumArtist tag, fall back to Artist, then "Unknown". */
export function effectiveAlbumArtist(t: Pick<Track, "albumArtist" | "artist">): string {
  return t.albumArtist?.trim() || t.artist?.trim() || "Unknown Artist";
}

/** Parse a leading 4-digit year out of an MPD date tag (e.g. "1999-10-04" -> 1999). */
export function parseYear(date: string | undefined): number | undefined {
  if (!date) return undefined;
  const m = /(\d{4})/.exec(date);
  if (!m) return undefined;
  const y = Number(m[1]);
  return Number.isFinite(y) ? y : undefined;
}

/** Sort key for tracks within an album: (disc, track, title). Matches gapless ordering. */
export function trackOrder(a: Track, b: Track): number {
  const disc = (a.discNo ?? 1) - (b.discNo ?? 1);
  if (disc !== 0) return disc;
  const num = (a.trackNo ?? 0) - (b.trackNo ?? 0);
  if (num !== 0) return num;
  return a.title.localeCompare(b.title);
}

/**
 * Aggregate a flat list of tracks into albums (by {@link makeAlbumKey}). Album
 * genre/year are taken from the first track that has them.
 */
export function albumsFromTracks(tracks: ReadonlyArray<Track>): Album[] {
  const byKey = new Map<string, Album>();
  for (const t of tracks) {
    const albumArtist = effectiveAlbumArtist(t);
    const title = t.album?.trim() || "Unknown Album";
    const key = makeAlbumKey(albumArtist, title);
    let album = byKey.get(key);
    if (!album) {
      album = { key, title, albumArtist };
      byKey.set(key, album);
    }
    if (album.genre === undefined && t.genre) album.genre = t.genre;
    if (album.year === undefined) {
      const y = parseYear(t.date);
      if (y !== undefined) album.year = y;
    }
  }
  return [...byKey.values()];
}
