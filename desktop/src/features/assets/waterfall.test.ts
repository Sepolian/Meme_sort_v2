import { describe, expect, it } from "vitest";
import {
  WATERFALL_FALLBACK_ASPECT_RATIO,
  assignWaterfallColumns,
  estimateWaterfallItemHeight,
  getAssetAspectRatio,
  getAssetAspectRatioStyle,
  getWaterfallColumnCount,
} from "./waterfall";

function dims(width: number | null, height: number | null) {
  return { width, height };
}

describe("waterfall aspect-ratio reservation (ticket 09)", () => {
  it("derives the ratio from valid AssetSummary dimensions", () => {
    expect(getAssetAspectRatio(dims(320, 180))).toBeCloseTo(320 / 180);
    expect(getAssetAspectRatioStyle(dims(320, 180))).toEqual({
      aspectRatio: "320 / 180",
    });
  });

  it("uses the documented 1:1 fallback for missing/invalid dimensions", () => {
    expect(WATERFALL_FALLBACK_ASPECT_RATIO).toBe(1);
    const invalid: Array<[number | null, number | null]> = [
      [null, 180],
      [320, null],
      [null, null],
      [0, 180],
      [320, 0],
      [0, 0],
      [-320, 180],
      [320, -180],
      [Number.NaN, 180],
      [320, Number.NaN],
      [Number.POSITIVE_INFINITY, 180],
      [320, Number.POSITIVE_INFINITY],
    ];
    for (const [width, height] of invalid) {
      expect(getAssetAspectRatio(dims(width, height))).toBe(
        WATERFALL_FALLBACK_ASPECT_RATIO,
      );
      // The fallback style is identical for every invalid shape so cards that
      // lack dimensions share one stable geometry.
      expect(getAssetAspectRatioStyle(dims(width, height))).toEqual({
        aspectRatio: "1 / 1",
      });
    }
  });

  it("estimates shorter columns from wider ratios (normalized width 1)", () => {
    const wide = estimateWaterfallItemHeight(dims(200, 100));
    const tall = estimateWaterfallItemHeight(dims(100, 200));
    const fallback = estimateWaterfallItemHeight(dims(null, null));
    expect(wide).toBeCloseTo(0.5);
    expect(tall).toBeCloseTo(2);
    expect(fallback).toBe(1);
    expect(wide).toBeLessThan(fallback);
    expect(fallback).toBeLessThan(tall);
  });
});

describe("waterfall column geometry (ticket 09)", () => {
  it("follows the comfortable 220px/12px contract", () => {
    expect(getWaterfallColumnCount(219, "comfortable")).toBe(1);
    expect(getWaterfallColumnCount(220, "comfortable")).toBe(1);
    // floor((452 + 12) / (220 + 12)) = 2.
    expect(getWaterfallColumnCount(452, "comfortable")).toBe(2);
    expect(getWaterfallColumnCount(1440, "comfortable")).toBe(6);
  });

  it("follows the compact 168px/8px contract", () => {
    expect(getWaterfallColumnCount(167, "compact")).toBe(1);
    expect(getWaterfallColumnCount(168, "compact")).toBe(1);
    // floor((520 + 8) / (168 + 8)) = 3.
    expect(getWaterfallColumnCount(520, "compact")).toBe(3);
  });

  it("collapses to one column when the container cannot be measured", () => {
    for (const width of [0, -10, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(getWaterfallColumnCount(width, "comfortable")).toBe(1);
      expect(getWaterfallColumnCount(width, "compact")).toBe(1);
    }
  });
});

describe("waterfall shortest-column placement (ticket 09)", () => {
  it("consumes items in order into the shortest column", () => {
    const columns = assignWaterfallColumns(
      ["a", "b", "c", "d"],
      2,
      (item) => ({ a: 2, b: 1, c: 1, d: 1 })[item]!,
    );
    // a -> col 0 (tie 0/0, leftmost); b -> col 1; c -> col 1 (2 vs 1);
    // d -> col 0 (tie 2/2, leftmost).
    expect(columns).toEqual([
      ["a", "d"],
      ["b", "c"],
    ]);
  });

  it("breaks height ties deterministically toward the leftmost column", () => {
    const first = assignWaterfallColumns([1, 2, 3, 4, 5, 6], 3, () => 1);
    expect(first).toEqual([
      [1, 4],
      [2, 5],
      [3, 6],
    ]);
    // Re-running the same input reproduces the same columns exactly.
    expect(assignWaterfallColumns([1, 2, 3, 4, 5, 6], 3, () => 1)).toEqual(
      first,
    );
  });

  it("keeps each column subsequence in input order (sorted order stays meaningful)", () => {
    const input = ["newest", "second", "third", "fourth", "fifth"];
    const heights = [0.5, 0.5, 2, 0.5, 0.5];
    const columns = assignWaterfallColumns(
      input,
      2,
      (_, index) => heights[index]!,
    );
    for (const column of columns) {
      const positions = column.map((item) => input.indexOf(item));
      expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    }
    // The earliest item always lands in the leftmost column.
    expect(columns[0]![0]).toBe("newest");
  });

  it("places tall items to balance columns across varied aspect heights", () => {
    const columns = assignWaterfallColumns(
      ["wide", "tall", "square"],
      2,
      (item) =>
        estimateWaterfallItemHeight(
          item === "wide"
            ? dims(400, 100)
            : item === "tall"
              ? dims(100, 400)
              : dims(200, 200),
        ),
    );
    // wide (0.25) -> col 0; tall (4) -> col 1; square (1) -> col 0 (0.25 < 4).
    expect(columns).toEqual([["wide", "square"], ["tall"]]);
  });

  it("treats non-finite and negative heights as zero without corrupting totals", () => {
    const columns = assignWaterfallColumns(
      ["a", "b", "c"],
      2,
      (item) => ({ a: Number.NaN, b: -5, c: 1 })[item]!,
    );
    // a -> col 0 (tie, leftmost; NaN counts 0); b -> col 0 (tie 0/0, leftmost;
    // negative counts 0); c -> col 0 (still tied 0/0, leftmost).
    expect(columns).toEqual([["a", "b", "c"], []]);
  });

  it("handles edge column counts and empty input", () => {
    expect(assignWaterfallColumns(["a", "b"], 1, () => 1)).toEqual([["a", "b"]]);
    // Degenerate counts collapse to a single column that preserves order.
    expect(assignWaterfallColumns(["a", "b"], 0, () => 1)).toEqual([["a", "b"]]);
    expect(assignWaterfallColumns(["a", "b"], -3, () => 1)).toEqual([
      ["a", "b"],
    ]);
    expect(assignWaterfallColumns([], 3, () => 1)).toEqual([[], [], []]);
  });
});
