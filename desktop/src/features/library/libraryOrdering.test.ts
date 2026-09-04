import { describe, expect, it } from "vitest";
import type { AssetSummary } from "../../api/types";
import {
  filterLibraryAssets,
  getAssetDisplayName,
  getOrderedLibraryAssets,
  isGifAsset,
  sortLibraryAssets,
} from "./libraryOrdering";

function makeAsset(overrides: Partial<AssetSummary> & { asset_id: string }): AssetSummary {
  return {
    library_path: `originals/${overrides.asset_id}.png`,
    library_url: `/media/originals/${overrides.asset_id}.png`,
    thumbnail_url: null,
    media_type: "image/png",
    content_hash: `hash-${overrides.asset_id}`,
    width: 100,
    height: 100,
    imported_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-09T00:00:00Z",
    source_record_count: 1,
    source_records: [{ source_path: `C:/Source/${overrides.asset_id}.png` }],
    status: "indexed",
    ...overrides,
  };
}

describe("libraryOrdering display names (ticket 08)", () => {
  it("prefers the first Source Record basename, supporting Windows separators", () => {
    const asset = makeAsset({
      asset_id: "a-1",
      library_path: "originals/library-name.png",
      source_records: [{ source_path: "C:\\Memes\\source-name.gif" }],
    });
    expect(getAssetDisplayName(asset)).toBe("source-name.gif");
  });

  it("falls back to the library path basename when no Source Record yields a name", () => {
    const asset = makeAsset({
      asset_id: "a-2",
      library_path: "originals/library-fallback.png",
      source_records: [],
    });
    expect(getAssetDisplayName(asset)).toBe("library-fallback.png");
  });

  it("falls back to the Asset ID when neither source nor library path yields a name", () => {
    const missing = makeAsset({
      asset_id: "missing-id-9",
      library_path: "",
      source_records: [],
    });
    expect(getAssetDisplayName(missing)).toBe("missing-id-9");

    const blankSegments = makeAsset({
      asset_id: "blank-id-1",
      library_path: "///",
      source_records: [{ source_path: "   " }],
    });
    expect(getAssetDisplayName(blankSegments)).toBe("blank-id-1");
  });
});

describe("libraryOrdering media detection (ticket 08)", () => {
  it("treats image/gif as GIF regardless of case", () => {
    expect(isGifAsset(makeAsset({ asset_id: "g-1", media_type: "image/gif" }))).toBe(true);
    expect(isGifAsset(makeAsset({ asset_id: "g-2", media_type: "IMAGE/GIF" }))).toBe(true);
    expect(isGifAsset(makeAsset({ asset_id: "s-1", media_type: "image/png" }))).toBe(false);
    expect(isGifAsset(makeAsset({ asset_id: "s-2", media_type: "image/jpeg" }))).toBe(false);
  });
});

describe("libraryOrdering filtering (ticket 08)", () => {
  const gifIndexed = makeAsset({ asset_id: "gif-indexed", media_type: "image/gif", status: "indexed" });
  const pngPending = makeAsset({ asset_id: "png-pending", media_type: "image/png", status: "pending" });
  const gifFailed = makeAsset({ asset_id: "gif-failed", media_type: "image/gif", status: "failed" });
  const all = [gifIndexed, pngPending, gifFailed];

  it("passes everything through when both filters are all", () => {
    expect(filterLibraryAssets(all, { media: "all", status: "all" }).map((a) => a.asset_id)).toEqual([
      "gif-indexed",
      "png-pending",
      "gif-failed",
    ]);
  });

  it("filters still vs gif", () => {
    expect(filterLibraryAssets(all, { media: "gif", status: "all" }).map((a) => a.asset_id)).toEqual([
      "gif-indexed",
      "gif-failed",
    ]);
    expect(filterLibraryAssets(all, { media: "still", status: "all" }).map((a) => a.asset_id)).toEqual([
      "png-pending",
    ]);
  });

  it("filters by status", () => {
    expect(filterLibraryAssets(all, { media: "all", status: "indexed" }).map((a) => a.asset_id)).toEqual([
      "gif-indexed",
    ]);
    expect(filterLibraryAssets(all, { media: "all", status: "pending" }).map((a) => a.asset_id)).toEqual([
      "png-pending",
    ]);
    expect(filterLibraryAssets(all, { media: "all", status: "failed" }).map((a) => a.asset_id)).toEqual([
      "gif-failed",
    ]);
  });

  it("composes media AND status rather than replacing each other", () => {
    expect(filterLibraryAssets(all, { media: "gif", status: "indexed" }).map((a) => a.asset_id)).toEqual([
      "gif-indexed",
    ]);
    expect(filterLibraryAssets(all, { media: "gif", status: "pending" })).toEqual([]);
    expect(filterLibraryAssets(all, { media: "still", status: "failed" })).toEqual([]);
  });

  it("does not mutate the input array", () => {
    const input = [...all];
    filterLibraryAssets(input, { media: "gif", status: "all" });
    expect(input.map((a) => a.asset_id)).toEqual(["gif-indexed", "png-pending", "gif-failed"]);
  });
});

describe("libraryOrdering sorting (ticket 08)", () => {
  it("sorts newest first by imported_at descending (newest is the default contract)", () => {
    const oldest = makeAsset({ asset_id: "old", imported_at: "2026-01-01T00:00:00Z" });
    const middle = makeAsset({ asset_id: "mid", imported_at: "2026-06-01T00:00:00Z" });
    const newest = makeAsset({ asset_id: "new", imported_at: "2026-08-01T00:00:00Z" });
    expect(sortLibraryAssets([oldest, newest, middle], "newest").map((a) => a.asset_id)).toEqual([
      "new",
      "mid",
      "old",
    ]);
  });

  it("sorts oldest first by imported_at ascending", () => {
    const oldest = makeAsset({ asset_id: "old", imported_at: "2026-01-01T00:00:00Z" });
    const middle = makeAsset({ asset_id: "mid", imported_at: "2026-06-01T00:00:00Z" });
    const newest = makeAsset({ asset_id: "new", imported_at: "2026-08-01T00:00:00Z" });
    expect(sortLibraryAssets([newest, oldest, middle], "oldest").map((a) => a.asset_id)).toEqual([
      "old",
      "mid",
      "new",
    ]);
  });

  it("treats unparsable timestamps as epoch 0 (last for newest, first for oldest)", () => {
    const valid = makeAsset({ asset_id: "valid", imported_at: "2026-08-01T00:00:00Z" });
    const broken = makeAsset({ asset_id: "broken", imported_at: "not-a-date" });
    expect(sortLibraryAssets([broken, valid], "newest").map((a) => a.asset_id)).toEqual([
      "valid",
      "broken",
    ]);
    expect(sortLibraryAssets([valid, broken], "oldest").map((a) => a.asset_id)).toEqual([
      "broken",
      "valid",
    ]);
  });

  it("sorts by display name ascending with numeric awareness", () => {
    const file10 = makeAsset({
      asset_id: "file10",
      library_path: "originals/file10.png",
      source_records: [{ source_path: "C:/Source/file10.png" }],
      imported_at: "2026-08-01T00:00:00Z",
    });
    const file2 = makeAsset({
      asset_id: "file2",
      library_path: "originals/file2.png",
      source_records: [{ source_path: "C:/Source/file2.png" }],
      imported_at: "2026-08-01T00:00:00Z",
    });
    const alpha = makeAsset({
      asset_id: "alpha",
      library_path: "originals/alpha.png",
      source_records: [{ source_path: "C:/Source/alpha.png" }],
      imported_at: "2026-08-01T00:00:00Z",
    });
    // Numeric: file2 < file10; alphabetical: alpha first.
    expect(sortLibraryAssets([file10, alpha, file2], "name").map((a) => a.asset_id)).toEqual([
      "alpha",
      "file2",
      "file10",
    ]);
  });

  it("sorts nameless Assets deterministically by Asset ID fallback", () => {
    const b = makeAsset({ asset_id: "b-id", library_path: "", source_records: [] });
    const a = makeAsset({ asset_id: "a-id", library_path: "", source_records: [] });
    expect(sortLibraryAssets([b, a], "name").map((x) => x.asset_id)).toEqual(["a-id", "b-id"]);
  });

  it("sorts by media_type ascending, then name, then newest", () => {
    const pngB = makeAsset({
      asset_id: "png-b",
      media_type: "image/png",
      library_path: "originals/b.png",
      source_records: [{ source_path: "C:/Source/b.png" }],
      imported_at: "2026-08-01T00:00:00Z",
    });
    const gifA = makeAsset({
      asset_id: "gif-a",
      media_type: "image/gif",
      library_path: "originals/a.gif",
      source_records: [{ source_path: "C:/Source/a.gif" }],
      imported_at: "2026-08-01T00:00:00Z",
    });
    const pngA = makeAsset({
      asset_id: "png-a",
      media_type: "image/png",
      library_path: "originals/a.png",
      source_records: [{ source_path: "C:/Source/a.png" }],
      imported_at: "2026-08-01T00:00:00Z",
    });
    expect(sortLibraryAssets([pngB, pngA, gifA], "type").map((a) => a.asset_id)).toEqual([
      "gif-a",
      "png-a",
      "png-b",
    ]);
  });

  it("sorts by status alphabetically (failed < indexed < pending), then name", () => {
    const pending = makeAsset({
      asset_id: "p",
      status: "pending",
      library_path: "originals/a.png",
      source_records: [{ source_path: "C:/Source/a.png" }],
    });
    const indexed = makeAsset({
      asset_id: "i",
      status: "indexed",
      library_path: "originals/b.png",
      source_records: [{ source_path: "C:/Source/b.png" }],
    });
    const failed = makeAsset({
      asset_id: "f",
      status: "failed",
      library_path: "originals/c.png",
      source_records: [{ source_path: "C:/Source/c.png" }],
    });
    expect(sortLibraryAssets([pending, failed, indexed], "status").map((a) => a.asset_id)).toEqual([
      "f",
      "i",
      "p",
    ]);
  });

  it("breaks full ties deterministically by Asset ID so placement never flips", () => {
    const first = makeAsset({ asset_id: "asset-001", imported_at: "2026-08-01T00:00:00Z" });
    const second = makeAsset({ asset_id: "asset-002", imported_at: "2026-08-01T00:00:00Z" });
    // Same timestamp, same derived name pattern, same type/status: ID decides.
    for (const sort of ["newest", "oldest", "name", "type", "status"] as const) {
      expect(sortLibraryAssets([second, first], sort).map((a) => a.asset_id)).toEqual([
        "asset-001",
        "asset-002",
      ]);
      // Re-sorting an already sorted input is idempotent.
      expect(sortLibraryAssets([first, second], sort).map((a) => a.asset_id)).toEqual([
        "asset-001",
        "asset-002",
      ]);
    }
  });

  it("does not mutate the input array", () => {
    const a = makeAsset({ asset_id: "a", imported_at: "2026-01-01T00:00:00Z" });
    const b = makeAsset({ asset_id: "b", imported_at: "2026-08-01T00:00:00Z" });
    const input = [a, b];
    sortLibraryAssets(input, "newest");
    expect(input.map((x) => x.asset_id)).toEqual(["a", "b"]);
  });
});

describe("libraryOrdering composition (ticket 08)", () => {
  it("applies sort after filtering to produce the waterfall input sequence", () => {
    const gifOld = makeAsset({
      asset_id: "gif-old",
      media_type: "image/gif",
      status: "indexed",
      imported_at: "2026-01-01T00:00:00Z",
      library_path: "originals/gif-old.gif",
      source_records: [{ source_path: "C:/Source/gif-old.gif" }],
    });
    const gifNew = makeAsset({
      asset_id: "gif-new",
      media_type: "image/gif",
      status: "indexed",
      imported_at: "2026-08-01T00:00:00Z",
      library_path: "originals/gif-new.gif",
      source_records: [{ source_path: "C:/Source/gif-new.gif" }],
    });
    const pngNewest = makeAsset({
      asset_id: "png-newest",
      media_type: "image/png",
      status: "indexed",
      imported_at: "2026-09-01T00:00:00Z",
      library_path: "originals/png-newest.png",
      source_records: [{ source_path: "C:/Source/png-newest.png" }],
    });
    const ordered = getOrderedLibraryAssets([pngNewest, gifOld, gifNew], {
      sort: "newest",
      media: "gif",
      status: "indexed",
    });
    // png-newest is filtered out, then gif-new (newer) sorts before gif-old.
    expect(ordered.map((a) => a.asset_id)).toEqual(["gif-new", "gif-old"]);
  });
});
