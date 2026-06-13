// Run with: node --test src/  (Node ≥23.6 strips types natively; hence the .ts import).
// Pure-logic tests for the genre/Soundtrack classification ported from WaveSafe.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isOST,
  ostSubgenreKey,
  ostSubgenreDisplayName,
  normalizedGenreKey,
  genreKey,
  raisableGenres,
} from "./genre.ts";

test("isOST: standard soundtrack genres", () => {
  for (const g of ["Soundtrack", "OST", "Original Score", "VGM", "video game music", "Cast Recording"]) {
    assert.equal(isOST(g), true, g);
  }
});

test("isOST: ost_ prefix convention", () => {
  assert.equal(isOST("ost_game"), true);
  assert.equal(isOST("OST_Anime"), true);
  assert.equal(isOST("ost_"), true); // prefix matches even with empty suffix
});

test("isOST: non-soundtracks and empties", () => {
  for (const g of ["Rock", "Hip-Hop", "Jazz", "", null, undefined]) {
    assert.equal(isOST(g as string), false, String(g));
  }
});

test("ostSubgenreKey: canonical mapping", () => {
  assert.equal(ostSubgenreKey("film score"), "film");
  assert.equal(ostSubgenreKey("game soundtrack"), "game");
  assert.equal(ostSubgenreKey("VGM"), "game");
  assert.equal(ostSubgenreKey("anime soundtrack"), "anime");
  assert.equal(ostSubgenreKey("television soundtrack"), "television");
  assert.equal(ostSubgenreKey("stage & screen"), "stage");
  assert.equal(ostSubgenreKey("soundtrack"), "soundtrack");
});

test("ostSubgenreKey: ost_ prefix suffix", () => {
  assert.equal(ostSubgenreKey("ost_game"), "game");
  assert.equal(ostSubgenreKey("ost_my_custom"), "my_custom");
  assert.equal(ostSubgenreKey("ost_"), null);
  assert.equal(ostSubgenreKey("Rock"), null);
});

test("ostSubgenreDisplayName", () => {
  assert.equal(ostSubgenreDisplayName("ost_my_custom"), "My Custom");
  assert.equal(ostSubgenreDisplayName("film score"), "Film");
  assert.equal(ostSubgenreDisplayName("Rock"), null);
});

test("normalizedGenreKey collapses formatting", () => {
  assert.equal(normalizedGenreKey("Hip-Hop"), "hiphop");
  assert.equal(normalizedGenreKey("hip hop"), "hiphop");
  assert.equal(normalizedGenreKey("HipHop"), "hiphop");
  assert.equal(normalizedGenreKey("  R&B  "), "rb");
  assert.equal(normalizedGenreKey("!!!"), null);
});

test("genreKey is null for OST, set for normal genres", () => {
  assert.equal(genreKey("Soundtrack"), null);
  assert.equal(genreKey("ost_game"), null);
  assert.equal(genreKey("Hip-Hop"), "hiphop");
  assert.equal(genreKey(undefined), null);
});

test("raisableGenres groups, counts, picks common spelling, excludes OST", () => {
  const albums = [
    { genre: "Hip-Hop" },
    { genre: "hip hop" },
    { genre: "Hip-Hop" },
    { genre: "Rock" },
    { genre: "Soundtrack" }, // excluded (OST)
    { genre: "ost_game" }, // excluded (OST)
    { genre: null }, // excluded
  ];
  const result = raisableGenres(albums);
  assert.equal(result.length, 2);
  assert.deepEqual(result[0], { key: "hiphop", displayName: "Hip-Hop", count: 3 });
  assert.deepEqual(result[1], { key: "rock", displayName: "Rock", count: 1 });
});
