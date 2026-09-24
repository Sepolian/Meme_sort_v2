import { expect, it } from "vitest";
import type { AssetSummary } from "../../api/types";
import { getAssetDisplayName, isGifAsset, sortLibraryAssets } from "./libraryOrdering";

function asset(id: string, overrides: Partial<AssetSummary> = {}): AssetSummary {
  return {
    asset_id: id,
    library_path: `originals/${id}.png`,
    library_url: `/media/originals/${id}.png`,
    thumbnail_url: null,
    media_type: "image/png",
    content_hash: `hash-${id}`,
    width: 100,
    height: 100,
    imported_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-09T00:00:00Z",
    source_record_count: 1,
    source_records: [{ source_path: `C:/Source/${id}.png` }],
    status: "indexed",
    ...overrides,
  };
}

const ids = (assets: AssetSummary[]) => assets.map((item) => item.asset_id);

it("uses a Windows source basename, then Library path, then Asset ID for display names", () => {
  expect(getAssetDisplayName(asset("a", {
    library_path: "originals/library.png", source_records: [{ source_path: "C:\\Memes\\source.gif" }],
  }))).toBe("source.gif");
  expect(getAssetDisplayName(asset("b", {
    library_path: "originals/library.png", source_records: [],
  }))).toBe("library.png");
  expect(getAssetDisplayName(asset("c", {
    library_path: "///", source_records: [{ source_path: "   " }],
  }))).toBe("c");
});

it("recognizes GIF media types regardless of case", () => {
  expect(isGifAsset(asset("gif", { media_type: "IMAGE/GIF" }))).toBe(true);
  expect(isGifAsset(asset("still", { media_type: "image/png" }))).toBe(false);
});

it("places invalid dates last for newest and first for oldest", () => {
  const valid = asset("valid", { imported_at: "2026-08-01T00:00:00Z" });
  const broken = asset("broken", { imported_at: "not-a-date" });
  expect(ids(sortLibraryAssets([broken, valid], "newest"))).toEqual(["valid", "broken"]);
  expect(ids(sortLibraryAssets([valid, broken], "oldest"))).toEqual(["broken", "valid"]);
});

it("sorts names with numeric awareness", () => {
  const file10 = asset("file10", { source_records: [{ source_path: "C:/Source/file10.png" }] });
  const file2 = asset("file2", { source_records: [{ source_path: "C:/Source/file2.png" }] });
  expect(ids(sortLibraryAssets([file10, file2], "name"))).toEqual(["file2", "file10"]);
});

it("sorts by media type then name", () => {
  const pngB = asset("png-b", { source_records: [{ source_path: "C:/Source/b.png" }] });
  const pngA = asset("png-a", { source_records: [{ source_path: "C:/Source/a.png" }] });
  const gif = asset("gif", { media_type: "image/gif" });
  expect(ids(sortLibraryAssets([pngB, pngA, gif], "type"))).toEqual(["gif", "png-a", "png-b"]);
});

it("sorts by status", () => {
  const pending = asset("pending", { status: "pending" });
  const indexed = asset("indexed", { status: "indexed" });
  const failed = asset("failed", { status: "failed" });
  expect(ids(sortLibraryAssets([pending, indexed, failed], "status"))).toEqual(["failed", "indexed", "pending"]);
});

it("breaks actual full ties by Asset ID without mutating input", () => {
  const common = { library_path: "originals/same.png", source_records: [{ source_path: "C:/Source/same.png" }] };
  const second = asset("asset-002", common);
  const first = asset("asset-001", common);
  const input = [second, first];
  for (const sort of ["newest", "oldest", "name", "type", "status"] as const) {
    expect(ids(sortLibraryAssets(input, sort))).toEqual(["asset-001", "asset-002"]);
  }
  expect(ids(input)).toEqual(["asset-002", "asset-001"]);
});
