/**
 * Genre & Soundtrack (OST) classification.
 *
 * Ported VERBATIM (as pure string logic) from the WaveSafe Swift app
 * (`Model/Album.swift` + `Model/GenreSupport.swift`). MPD is never asked to
 * understand any of this — it only supplies the raw `genre` tag. Anything beyond
 * file tags (custom overrides) lives in MPD stickers; raised-genre prefs live in
 * shared config. Keeping this here means the desktop app and QAM plugin classify
 * identically and can never diverge.
 *
 * Two conventions are detected:
 *   1. `ost_<subgenre>` prefix (e.g. `ost_game`, `ost_anime`) — custom tagging.
 *   2. Standard/common soundtrack genre strings from metadata.
 */

/** Lowercased genre strings treated as Soundtracks. Mirrors `soundtrackGenreSet`. */
const SOUNDTRACK_GENRES: ReadonlySet<string> = new Set([
  "soundtrack", "soundtracks", "ost", "original soundtrack",
  "original score", "score",
  "film score",
  "game soundtrack", "game ost", "game score", "video game", "video game music", "videogame", "vgm",
  "anime soundtrack",
  "tv soundtrack", "television soundtrack",
  "musical", "stage & screen", "stage and screen", "cast recording",
]);

/** Mirrors `canonicalSubgenreKey(for:)`. */
function canonicalSubgenreKey(lowercasedGenre: string): string {
  switch (lowercasedGenre) {
    case "film score":
      return "film";
    case "game soundtrack":
    case "game ost":
    case "game score":
    case "video game":
    case "video game music":
    case "videogame":
    case "vgm":
      return "game";
    case "anime soundtrack":
      return "anime";
    case "tv soundtrack":
    case "television soundtrack":
      return "television";
    case "musical":
    case "stage & screen":
    case "stage and screen":
    case "cast recording":
      return "stage";
    default:
      return "soundtrack";
  }
}

/** True if the genre marks the album as a Soundtrack. Mirrors `Album.isOST`. */
export function isOST(genre: string | null | undefined): boolean {
  if (!genre) return false;
  const g = genre.toLowerCase();
  return g.startsWith("ost_") || SOUNDTRACK_GENRES.has(g);
}

/** Canonical Soundtrack subgenre key, or null. Mirrors `Album.ostSubgenreKey`. */
export function ostSubgenreKey(genre: string | null | undefined): string | null {
  if (!genre) return null;
  const lower = genre.toLowerCase();
  if (lower.startsWith("ost_")) {
    const suffix = lower.slice(4);
    return suffix.length === 0 ? null : suffix;
  }
  if (SOUNDTRACK_GENRES.has(lower)) {
    return canonicalSubgenreKey(lower);
  }
  return null;
}

/** Human-readable subgenre name. Mirrors `Album.ostSubgenreDisplayName` (Swift `.capitalized`). */
export function ostSubgenreDisplayName(genre: string | null | undefined): string | null {
  const key = ostSubgenreKey(genre);
  if (key === null) return null;
  return capitalizeWords(key.replace(/_/g, " "));
}

/**
 * Grouping key that collapses formatting differences (case, spacing,
 * punctuation): "Hip-Hop", "hip hop", "HipHop" all map to "hiphop".
 * Mirrors `Album.normalizedGenreKey(_:)` (CharacterSet.alphanumerics, Unicode-aware).
 */
export function normalizedGenreKey(raw: string): string | null {
  const key = raw.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  return key.length === 0 ? null : key;
}

/**
 * Normalized genre key for non-OST albums; null for OST albums (they stay only
 * under Soundtracks) or albums without a usable genre. Mirrors `Album.genreKey`.
 */
export function genreKey(genre: string | null | undefined): string | null {
  if (isOST(genre) || !genre) return null;
  return normalizedGenreKey(genre);
}

/** Swift `String.capitalized`: first letter of each space-separated word upper, rest lower. */
function capitalizeWords(s: string): string {
  return s
    .split(" ")
    .map((w) => (w.length === 0 ? w : w[0]!.toUpperCase() + w.slice(1).toLowerCase()))
    .join(" ");
}

export interface GenreInfo {
  key: string;
  displayName: string;
  count: number;
}

/**
 * Distinct non-OST genres present, grouped by normalized key. Display name is the
 * most common raw spelling in each group. Sorted by count desc, then name.
 * Mirrors `Library.raisableGenres()`.
 */
export function raisableGenres(albums: ReadonlyArray<{ genre?: string | null }>): GenreInfo[] {
  const groups = new Map<string, { count: number; rawCounts: Map<string, number> }>();
  for (const album of albums) {
    const key = genreKey(album.genre);
    if (key === null) continue;
    let group = groups.get(key);
    if (!group) {
      group = { count: 0, rawCounts: new Map() };
      groups.set(key, group);
    }
    group.count++;
    const raw = album.genre ?? "";
    if (raw.length > 0) group.rawCounts.set(raw, (group.rawCounts.get(raw) ?? 0) + 1);
  }

  const infos: GenreInfo[] = [];
  for (const [key, group] of groups) {
    let displayName = key;
    let best = -1;
    for (const [raw, c] of group.rawCounts) {
      // Most common spelling wins; ties broken toward the lexicographically smaller spelling.
      if (c > best || (c === best && raw < displayName)) {
        best = c;
        displayName = raw;
      }
    }
    infos.push({ key, displayName, count: group.count });
  }

  infos.sort((a, b) =>
    a.count !== b.count
      ? b.count - a.count
      : a.displayName.localeCompare(b.displayName, undefined, { sensitivity: "accent" }),
  );
  return infos;
}
