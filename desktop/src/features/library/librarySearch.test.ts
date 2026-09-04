import { describe, expect, it } from "vitest";
import type { AssetSummary } from "../../api/types";
import { filterLocalAssets, matchesLocalQuery } from "./librarySearch";

function summary(overrides: Partial<AssetSummary> & { asset_id: string }): AssetSummary {
  return {
    library_path: "originals/fallback.png",
    library_url: "/media/originals/fallback.png",
    thumbnail_url: "/media/thumbnails/fallback.jpg",
    media_type: "image/png",
    content_hash: "hash",
    width: 100,
    height: 100,
    imported_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-09T00:00:00Z",
    source_record_count: 1,
    source_records: [{ source_path: "C:/Source/fallback.png" }],
    status: "indexed",
    ...overrides,
  };
}

describe("matchesLocalQuery (ticket 11)", () => {
  it("matches displayed names case-insensitively", () => {
    const asset = summary({
      asset_id: "a1",
      library_path: "originals/other.png",
      source_records: [{ source_path: "C:/Source/Cat-Meme.png" }],
    });
    expect(matchesLocalQuery(asset, "cat")).toBe(true);
    expect(matchesLocalQuery(asset, "CAT-MEME")).toBe(true);
    expect(matchesLocalQuery(asset, "CaT")).toBe(true);
    expect(matchesLocalQuery(asset, "dog")).toBe(false);
  });

  it("falls back to library_path basename when no Source Record is available", () => {
    const asset = summary({
      asset_id: "a1b",
      library_path: "originals/Cat-Meme.png",
      source_records: [],
    });
    expect(matchesLocalQuery(asset, "cat")).toBe(true);
  });

  it("matches the available/primary Source Path case-insensitively", () => {
    const asset = summary({
      asset_id: "a2",
      library_path: "originals/unrelated.png",
      source_records: [{ source_path: "C:/Source/Dog-Park.png" }],
    });
    expect(matchesLocalQuery(asset, "dog-park")).toBe(true);
    expect(matchesLocalQuery(asset, "DOG-PARK")).toBe(true);
    expect(matchesLocalQuery(asset, "c:/source/dog")).toBe(true);
  });

  it("covers only displayed name and primary Source Path", () => {
    const asset = summary({
      asset_id: "unique-asset-id-12345",
      library_path: "originals/plain.png",
      media_type: "image/png",
      status: "indexed",
      source_records: [{ source_path: "C:/Source/plain.png" }],
    });
    // Status, media type, and Asset ID substrings must not match.
    expect(matchesLocalQuery(asset, "indexed")).toBe(false);
    expect(matchesLocalQuery(asset, "image/png")).toBe(false);
    expect(matchesLocalQuery(asset, "unique-asset-id")).toBe(false);
  });

  it("includes Pending and Failed Assets (no status filtering here)", () => {
    const pending = summary({ asset_id: "p1", library_path: "originals/dog.png", source_records: [{ source_path: "C:/Source/dog.png" }], status: "pending" });
    const failed = summary({ asset_id: "f1", library_path: "originals/bird.png", source_records: [{ source_path: "C:/Source/bird.png" }], status: "failed" });
    expect(matchesLocalQuery(pending, "dog")).toBe(true);
    expect(matchesLocalQuery(failed, "bird")).toBe(true);
  });

  it("treats empty queries as matching everything", () => {
    const asset = summary({ asset_id: "a3" });
    expect(matchesLocalQuery(asset, "")).toBe(true);
    expect(matchesLocalQuery(asset, "   ")).toBe(true);
  });
});

describe("filterLocalAssets", () => {
  it("preserves input order for matches", () => {
    const first = summary({ asset_id: "b1", library_path: "originals/cat-one.png", source_records: [{ source_path: "C:/Source/cat-one.png" }] });
    const second = summary({ asset_id: "b2", library_path: "originals/cat-two.png", source_records: [{ source_path: "C:/Source/cat-two.png" }] });
    const other = summary({ asset_id: "b3", library_path: "originals/dog.png", source_records: [{ source_path: "C:/Source/dog.png" }] });
    const filtered = filterLocalAssets([second, first, other], "cat");
    expect(filtered.map((a) => a.asset_id)).toEqual(["b2", "b1"]);
  });

  it("returns a copy of the input when the query is empty", () => {
    const assets = [summary({ asset_id: "c1" }), summary({ asset_id: "c2" })];
    const filtered = filterLocalAssets(assets, "");
    expect(filtered.map((a) => a.asset_id)).toEqual(["c1", "c2"]);
    expect(filtered).not.toBe(assets);
  });
});
