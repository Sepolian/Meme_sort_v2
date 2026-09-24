import { beforeEach, expect, it } from "vitest";
import {
  LIBRARY_PREFERENCE_KEYS,
  loadLibraryPreferences,
  normalizeLibrarySearchString,
  parseLibraryUrlState,
  saveLibraryPreferences,
  serializeLibraryUrlState,
} from "./libraryUrlState";

beforeEach(() => localStorage.clear());

it("rejects invalid URL filters and empty inspector targets", () => {
  const parsed = parseLibraryUrlState("?sort=bogus&media=raw&status=nope&asset=%20");
  expect(parsed).toMatchObject({ sort: null, media: null, status: null, assetId: null });
});

it("round trips query text containing URL syntax and non-ASCII characters", () => {
  const query = "a&b=c+d 中文 meme";
  expect(parseLibraryUrlState(serializeLibraryUrlState({ q: query })).q).toBe(query);
});

it("omits default values but serializes explicit Library state", () => {
  expect(serializeLibraryUrlState({ q: "", sort: "newest", media: "all", status: "all", assetId: null }).toString()).toBe("");
  expect(serializeLibraryUrlState({ q: "cat", sort: "oldest", media: "gif", status: "failed", assetId: "id-1" }).toString())
    .toBe("q=cat&sort=oldest&media=gif&status=failed&asset=id-1");
});

it("normalizes invalid filter params while preserving valid query and inspector state", () => {
  const normalized = normalizeLibrarySearchString("?sort=bogus&q=keep&media=gif&status=nope&asset=keep-me");
  expect(parseLibraryUrlState(normalized)).toMatchObject({
    q: "keep", sort: null, media: "gif", status: null, assetId: "keep-me",
  });
});

it("falls back to defaults for corrupt persisted preferences", () => {
  localStorage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "obsolete-value");
  localStorage.setItem(LIBRARY_PREFERENCE_KEYS.media, "RAW");
  localStorage.setItem(LIBRARY_PREFERENCE_KEYS.status, "");
  localStorage.setItem(LIBRARY_PREFERENCE_KEYS.density, "cozy");
  expect(loadLibraryPreferences()).toEqual({
    sort: "newest", media: "all", status: "all", density: "comfortable",
  });
});

it("persists partial updates without clobbering other Library preferences", () => {
  saveLibraryPreferences({ sort: "name" });
  saveLibraryPreferences({ density: "compact" });
  expect(loadLibraryPreferences()).toMatchObject({ sort: "name", density: "compact" });
});
