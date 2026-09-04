import { act, fireEvent, render, screen } from "@testing-library/react";
import type { RefObject } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssetWaterfall } from "./AssetWaterfall";
import type { AssetSummary } from "../../api/types";
import type { LibraryDensity } from "../library/libraryUrlState";
import {
  assignWaterfallColumns,
  estimateWaterfallItemHeight,
} from "./waterfall";

type IntersectCallback = (
  entries: Array<{ isIntersecting: boolean }>,
) => void;

let intersectCallbacks: IntersectCallback[] = [];

class MockIntersectionObserver {
  constructor(callback: IntersectCallback) {
    intersectCallbacks.push(callback);
  }

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}
}

function makeAsset(
  overrides: Partial<AssetSummary> & { asset_id: string },
): AssetSummary {
  const id = overrides.asset_id;
  return {
    library_path: `originals/${id}.png`,
    library_url: `/media/originals/${id}.png`,
    thumbnail_url: `/media/thumbnails/${id}.jpg`,
    media_type: "image/png",
    content_hash: `hash-${id}`,
    width: 200,
    height: 100,
    imported_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    source_record_count: 1,
    source_records: [{ source_path: `C:/Source/${id}.png` }],
    status: "indexed",
    ...overrides,
  };
}

function makeGif(id: string): AssetSummary {
  return makeAsset({
    asset_id: id,
    library_path: `originals/${id}.gif`,
    library_url: `/media/originals/${id}.gif`,
    thumbnail_url: `/media/thumbnails/${id}.jpg`,
    media_type: "image/gif",
    source_records: [{ source_path: `C:/Source/${id}.gif` }],
  });
}

function renderWaterfall(options: {
  assets: AssetSummary[];
  density?: LibraryDensity;
  columnCount?: number;
  checkedIds?: ReadonlySet<string>;
  onOpenAsset?: (assetId: string) => void;
  onToggleChecked?: (assetId: string) => void;
}) {
  return render(
    <AssetWaterfall
      assets={options.assets}
      density={options.density ?? "comfortable"}
      checkedIds={options.checkedIds ?? new Set()}
      onOpenAsset={options.onOpenAsset ?? (() => undefined)}
      onToggleChecked={options.onToggleChecked ?? (() => undefined)}
      columnCount={options.columnCount}
    />,
  );
}

function intersectAll(isIntersecting: boolean): void {
  // State updates must flush synchronously inside act(); otherwise React
  // batches them past the assertions below.
  act(() => {
    for (const callback of [...intersectCallbacks]) {
      callback([{ isIntersecting }]);
    }
  });
}

function cardById(container: HTMLElement, assetId: string): HTMLElement {
  const card = container.querySelector(
    `article[data-asset-id="${assetId}"]`,
  );
  if (!card || !(card instanceof HTMLElement)) {
    throw new Error(`missing card for ${assetId}`);
  }
  return card;
}

function cardImgSrc(container: HTMLElement, assetId: string): string | null | undefined {
  return cardById(container, assetId).querySelector("img")?.getAttribute("src");
}

describe("AssetWaterfall layout and lazy media (ticket 09)", () => {
  beforeEach(() => {
    intersectCallbacks = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("places cards into deterministic shortest columns with reserved geometry", () => {
    const assets = [
      makeAsset({ asset_id: "a-wide", width: 400, height: 100 }),
      makeAsset({ asset_id: "b-tall", width: 100, height: 400 }),
      makeAsset({ asset_id: "c-square", width: 200, height: 200 }),
      makeAsset({ asset_id: "d-missing", width: null, height: null }),
    ];
    const { container } = renderWaterfall({ assets, columnCount: 2 });

    const expected = assignWaterfallColumns(assets, 2, (item) =>
      estimateWaterfallItemHeight(item),
    );
    expect(expected.map((column) => column.map((asset) => asset.asset_id))).toEqual([
      ["a-wide", "c-square", "d-missing"],
      ["b-tall"],
    ]);

    for (const [columnIndex, column] of expected.entries()) {
      for (const asset of column) {
        expect(cardById(container, asset.asset_id).dataset.column).toBe(
          String(columnIndex),
        );
      }
    }

    const section = screen.getByRole("region", { name: "Assets" });
    expect(section).toHaveAttribute("data-density", "comfortable");
    expect(section).toHaveAttribute("data-column-count", "2");

    // Every card reserves its aspect ratio before media loads, including the
    // documented 1:1 fallback for missing dimensions.
    const reservations = new Map(
      [...container.querySelectorAll(".asset-card-media-wrap")].map((wrap) => [
        wrap.getAttribute("data-asset-id"),
        (wrap as HTMLElement).style.aspectRatio,
      ]),
    );
    expect(reservations.get("a-wide")).toBe("400 / 100");
    expect(reservations.get("b-tall")).toBe("100 / 400");
    expect(reservations.get("c-square")).toBe("200 / 200");
    expect(reservations.get("d-missing")).toBe("1 / 1");
  });

  it("does not request media outside the viewport margin until it is near", () => {
    const assets = [makeAsset({ asset_id: "a" }), makeAsset({ asset_id: "b" })];
    const { container } = renderWaterfall({ assets, columnCount: 2 });

    // Cards and placeholders render, but no image source is requested.
    expect(container.querySelectorAll("article.asset-card")).toHaveLength(2);
    expect(container.querySelectorAll(".asset-card-media-wrap")).toHaveLength(2);
    expect(container.querySelectorAll("img")).toHaveLength(0);

    intersectAll(true);

    const sources = [...container.querySelectorAll("img")].map((img) =>
      img.getAttribute("src"),
    );
    expect(sources).toEqual([
      "http://memesort-media.localhost/media/thumbnails/a.jpg",
      "http://memesort-media.localhost/media/thumbnails/b.jpg",
    ]);
  });

  it("keeps Pending/Failed badges visible and hides normal Indexed state", () => {
    const assets = [
      makeAsset({ asset_id: "pending-one", status: "pending" }),
      makeAsset({ asset_id: "failed-one", status: "failed" }),
      makeAsset({ asset_id: "indexed-one", status: "indexed" }),
    ];
    const { container } = renderWaterfall({ assets, columnCount: 1 });
    intersectAll(true);

    expect(
      cardById(container, "pending-one").querySelector(".status-pill"),
    ).toHaveTextContent("Pending Asset");
    expect(
      cardById(container, "failed-one").querySelector(".status-pill"),
    ).toHaveTextContent("Failed Asset");
    expect(
      cardById(container, "indexed-one").querySelector(".status-pill"),
    ).toBeNull();
  });

  it("shows name, quick actions, and selection checkbox on hover content", () => {
    const assets = [makeAsset({ asset_id: "hover-me" })];
    const onOpenAsset = vi.fn();
    const onToggleChecked = vi.fn();
    renderWaterfall({
      assets,
      columnCount: 1,
      checkedIds: new Set(),
      onOpenAsset,
      onToggleChecked,
    });

    // The open control keeps its accessible name for existing toolbar tests.
    fireEvent.click(screen.getByRole("button", { name: "Open hover-me.png" }));
    expect(onOpenAsset).toHaveBeenCalledWith("hover-me");

    // Hover content carries the visible name, one quick action, and the card
    // checkbox without touching inspector/selection-ticket scope.
    expect(screen.getByText("hover-me.png")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Quick actions for hover-me.png"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(onOpenAsset).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByLabelText("Select hover-me.png"));
    expect(onToggleChecked).toHaveBeenCalledWith("hover-me");
  });

  it("renders uncropped thumbnails that fill the reserved wrapper", () => {
    const assets = [makeAsset({ asset_id: "tall", width: 100, height: 400 })];
    const { container } = renderWaterfall({ assets, columnCount: 1 });
    intersectAll(true);

    const img = cardById(container, "tall").querySelector(
      "img.asset-card-media",
    );
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toBe(
      "http://memesort-media.localhost/media/thumbnails/tall.jpg",
    );
  });
});

describe("AssetWaterfall singleton GIF hover (ticket 09)", () => {
  beforeEach(() => {
    intersectCallbacks = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("swaps only the hovered GIF to its managed URL and restores the previous card", () => {
    const assets = [makeGif("gif-a"), makeGif("gif-b"), makeAsset({ asset_id: "still" })];
    const { container } = renderWaterfall({ assets, columnCount: 2 });
    intersectAll(true);

    const thumbnailA = "http://memesort-media.localhost/media/thumbnails/gif-a.jpg";
    const animatedA = "http://memesort-media.localhost/media/originals/gif-a.gif";
    const thumbnailB = "http://memesort-media.localhost/media/thumbnails/gif-b.jpg";
    const animatedB = "http://memesort-media.localhost/media/originals/gif-b.gif";
    const stillSrc = "http://memesort-media.localhost/media/thumbnails/still.jpg";

    // Static thumbnails by default: an image-source swap, not a video API.
    expect(cardImgSrc(container, "gif-a")).toBe(thumbnailA);
    expect(cardImgSrc(container, "gif-b")).toBe(thumbnailB);
    expect(cardImgSrc(container, "still")).toBe(stillSrc);

    fireEvent.mouseEnter(cardById(container, "gif-a"));
    expect(cardImgSrc(container, "gif-a")).toBe(animatedA);
    expect(cardImgSrc(container, "gif-b")).toBe(thumbnailB);

    // Hovering another GIF first restores the previous card: at most one
    // animated source is ever active.
    fireEvent.mouseEnter(cardById(container, "gif-b"));
    expect(cardImgSrc(container, "gif-a")).toBe(thumbnailA);
    expect(cardImgSrc(container, "gif-b")).toBe(animatedB);
    expect(
      container.querySelectorAll('article[data-gif-active="true"]'),
    ).toHaveLength(1);

    fireEvent.mouseLeave(cardById(container, "gif-b"));
    expect(cardImgSrc(container, "gif-b")).toBe(thumbnailB);
    expect(
      container.querySelectorAll('article[data-gif-active="true"]'),
    ).toHaveLength(0);
  });
});

describe("AssetWaterfall large-Library stability (ticket 09)", () => {
  beforeEach(() => {
    intersectCallbacks = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a 3,000-Asset fixture without eager media and with stable geometry", () => {
    const assets: AssetSummary[] = Array.from({ length: 3000 }, (_, index) => {
      const id = `fixture-${String(index).padStart(4, "0")}`;
      return makeAsset({
        asset_id: id,
        library_path: `originals/${id}.png`,
        library_url: `/media/originals/${id}.png`,
        thumbnail_url: `/media/thumbnails/${id}.jpg`,
        media_type: index % 5 === 0 ? "image/gif" : "image/png",
        // Every 7th Asset lacks dimensions (fallback path); every 11th has a
        // zero height (invalid path). Both must reserve the same geometry.
        width: index % 7 === 0 ? null : 100 + ((index * 37) % 400),
        height: index % 11 === 0 ? 0 : 100 + ((index * 53) % 300),
        status: index % 9 === 0 ? "pending" : "indexed",
        source_records: [{ source_path: `C:/Source/${id}.png` }],
      });
    });
    const { container } = renderWaterfall({ assets, columnCount: 4 });

    expect(container.querySelectorAll("article.asset-card")).toHaveLength(3000);
    // Nothing outside the viewport margin is requested eagerly.
    expect(container.querySelectorAll("img")).toHaveLength(0);

    const wraps = container.querySelectorAll(".asset-card-media-wrap");
    expect(wraps).toHaveLength(3000);
    let fallbackCount = 0;
    wraps.forEach((wrap) => {
      const ratio = (wrap as HTMLElement).style.aspectRatio;
      expect(ratio).toBeTruthy();
      if (ratio === "1 / 1") fallbackCount += 1;
    });
    // Assets missing dimensions or carrying a zero height share the fallback.
    expect(fallbackCount).toBeGreaterThan(600);

    // Once cards approach the viewport their sources resolve, still through
    // the reserved wrappers (geometry never changes on load).
    intersectAll(true);
    expect(container.querySelectorAll("img")).toHaveLength(3000);
    expect(
      container.querySelectorAll(".asset-card-media-wrap"),
    ).toHaveLength(3000);
  });
});

describe("AssetWaterfall scroll preservation (ticket 09)", () => {
  beforeEach(() => {
    intersectCallbacks = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores the Library scroll position when surrounding state changes in place", () => {
    const assets = [makeGif("gif-scroll"), makeAsset({ asset_id: "still-scroll" })];
    // Scroll preservation reads the parent-owned wall ref, exactly as
    // AssetsWorkspace provides it for native-drag hit-testing.
    const sectionRef: RefObject<HTMLDivElement | null> = {
      current: null,
    };
    const { container } = render(
      <div className="library-content">
        <AssetWaterfall
          assets={assets}
          density="comfortable"
          checkedIds={new Set()}
          onOpenAsset={() => undefined}
          onToggleChecked={() => undefined}
          columnCount={1}
          sectionRef={sectionRef}
        />
      </div>,
    );
    const scroller = container.querySelector(
      ".library-content",
    ) as HTMLElement;

    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    // Simulate DOM churn resetting the container without a route replacement
    // (route changes unmount the waterfall and intentionally drop the slot).
    scroller.scrollTop = 0;

    // Any surrounding in-place state change (here: GIF hover) re-renders and
    // restores the saved position instead of stranding the user at the top.
    fireEvent.mouseEnter(cardById(container, "gif-scroll"));
    expect(scroller.scrollTop).toBe(100);
  });
});
