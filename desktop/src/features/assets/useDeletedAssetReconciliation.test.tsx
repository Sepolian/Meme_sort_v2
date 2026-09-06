import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useDeletedAssetReconciliation } from "./useDeletedAssetReconciliation";
import type { AssetListResult } from "../../api/types";

const FIRST_ASSET = "123e4567-e89b-12d3-a456-426614174000";
const SECOND_ASSET = "123e4567-e89b-12d3-a456-426614174001";

function summary(assetId: string) {
  return {
    asset_id: assetId,
    library_path: `originals/${assetId}.png`,
    library_url: `/media/originals/${assetId}.png`,
    thumbnail_url: `/media/thumbnails/${assetId}.jpg`,
    media_type: "image/png",
    content_hash: `hash-${assetId}`,
    width: 160,
    height: 90,
    imported_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-09T00:00:00Z",
    source_record_count: 1,
    source_records: [{ source_path: `C:/Source/${assetId}.png` }],
    status: "indexed" as const,
  };
}

function libraryWith(assetIds: string[]): AssetListResult {
  return {
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    assets: assetIds.map(summary),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

interface HarnessOptions {
  getAssets: () => Promise<AssetListResult>;
  getAssetDetail: (assetId: string) => Promise<unknown>;
  initialAssetIds?: string[];
  detailAssetId?: string;
  inspectedAssetId?: string | null;
  /** Whether a detail is still subscribed, like an inspector left mounted. */
  observeDetail?: boolean;
}

function renderDeletion(options: HarnessOptions) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const initial = libraryWith(options.initialAssetIds ?? [FIRST_ASSET, SECOND_ASSET]);
  const detailAssetId = options.detailAssetId ?? FIRST_ASSET;
  queryClient.setQueryData(["assets"], initial);
  queryClient.setQueryData(["asset-detail", detailAssetId], {
    library_root: "C:/Library",
    asset: summary(detailAssetId),
  });

  // Only queries with subscribers are refreshed, so the harness mounts the two
  // queries the Library mounts. Closing the detail releases its subscription
  // exactly like unmounting the inspector does.
  const closeDetailRef: { current: (open: boolean) => void } = { current: () => undefined };
  const onCloseDetail = vi.fn(() => closeDetailRef.current(false));

  function Wall() {
    useQuery({ queryKey: ["assets"], queryFn: options.getAssets });
    return null;
  }
  function Detail() {
    useQuery({
      queryKey: ["asset-detail", detailAssetId],
      queryFn: () => options.getAssetDetail(detailAssetId),
    });
    return null;
  }
  function Wrapper({ children }: { children: ReactNode }) {
    const [detailOpen, setDetailOpen] = useState(true);
    closeDetailRef.current = setDetailOpen;
    return (
      <QueryClientProvider client={queryClient}>
        <Wall />
        {detailOpen && options.observeDetail !== false ? <Detail /> : null}
        {children}
      </QueryClientProvider>
    );
  }

  const view = renderHook(
    ({ inspectedAssetId }: { inspectedAssetId: string | null }) =>
      useDeletedAssetReconciliation({ inspectedAssetId, onCloseDetail }),
    {
      wrapper: Wrapper,
      initialProps: { inspectedAssetId: options.inspectedAssetId ?? detailAssetId },
    },
  );
  return { ...view, queryClient, detailAssetId, onCloseDetail };
}

describe("post-delete cache coordination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("applies the confirmed deletion to the visible list before the refresh settles", async () => {
    let refreshSettled = false;
    const gate = deferred<AssetListResult>();
    const { result, queryClient } = renderDeletion({
      getAssets: () => gate.promise.then((value) => {
        refreshSettled = true;
        return value;
      }),
      getAssetDetail: async () => ({ library_root: "C:/Library", asset: summary(FIRST_ASSET) }),
    });

    await act(async () => {
      void result.current([FIRST_ASSET]);
    });
    await waitFor(() => {
      expect((queryClient.getQueryData(["assets"]) as AssetListResult).assets.map((a) => a.asset_id)).toEqual([SECOND_ASSET]);
    });
    expect(refreshSettled).toBe(false);

    await act(async () => {
      gate.resolve(libraryWith([SECOND_ASSET]));
    });
    await waitFor(() => {
      expect(refreshSettled).toBe(true);
    });
    expect((queryClient.getQueryData(["assets"]) as AssetListResult).assets.map((a) => a.asset_id)).toEqual([SECOND_ASSET]);
  });

  it("drops the detail cache of a confirmed-deleted Asset", async () => {
    const { result, queryClient, detailAssetId } = renderDeletion({
      getAssets: async () => libraryWith([SECOND_ASSET]),
      getAssetDetail: async () => ({ library_root: "C:/Library", asset: summary(FIRST_ASSET) }),
      observeDetail: false,
    });

    await act(async () => {
      await result.current([FIRST_ASSET]);
    });

    expect(queryClient.getQueryData(["asset-detail", detailAssetId])).toBeUndefined();
  });

  it("never refreshes the detail of a confirmed-deleted Asset", async () => {
    const getAssetDetail = vi.fn(async () => ({ library_root: "C:/Library", asset: summary(FIRST_ASSET) }));
    const { result, onCloseDetail } = renderDeletion({
      getAssets: async () => libraryWith([SECOND_ASSET]),
      getAssetDetail,
    });

    // The detail was fetched once while it was open; the confirmed deletion
    // must not refresh it into an avoidable error.
    await waitFor(() => {
      expect(getAssetDetail).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await result.current([FIRST_ASSET]);
    });

    expect(onCloseDetail).toHaveBeenCalledTimes(1);
    expect(getAssetDetail).toHaveBeenCalledTimes(1);
  });

  it("closes the detail only while it still targets a deleted Asset", async () => {
    const { result, rerender, onCloseDetail } = renderDeletion({
      getAssets: async () => libraryWith([SECOND_ASSET]),
      getAssetDetail: async () => ({ library_root: "C:/Library", asset: summary(FIRST_ASSET) }),
      inspectedAssetId: FIRST_ASSET,
    });

    // The user moved to another Asset while the deletion was in flight.
    rerender({ inspectedAssetId: SECOND_ASSET });
    await act(async () => {
      await result.current([FIRST_ASSET]);
    });
    expect(onCloseDetail).not.toHaveBeenCalled();

    rerender({ inspectedAssetId: FIRST_ASSET });
    await act(async () => {
      await result.current([FIRST_ASSET]);
    });
    expect(onCloseDetail).toHaveBeenCalledTimes(1);
  });

  it("keeps the confirmed deletion when the following refresh fails", async () => {
    const { result, queryClient } = renderDeletion({
      getAssets: async () => {
        throw new Error("sidecar unavailable");
      },
      getAssetDetail: async () => ({ library_root: "C:/Library", asset: summary(FIRST_ASSET) }),
    });

    let rejection: unknown = null;
    await act(async () => {
      await result.current([FIRST_ASSET]).catch((error) => {
        rejection = error;
      });
    });

    expect(rejection).toBeNull();
    expect((queryClient.getQueryData(["assets"]) as AssetListResult).assets.map((a) => a.asset_id)).toEqual([SECOND_ASSET]);
  });

  it("keeps nothing removed but still refresches when no Asset was deleted", async () => {
    const getAssets = vi.fn(async () => libraryWith([FIRST_ASSET, SECOND_ASSET]));
    const { result, queryClient } = renderDeletion({
      getAssets,
      getAssetDetail: async () => ({ library_root: "C:/Library", asset: summary(FIRST_ASSET) }),
    });
    const before = queryClient.getQueryData(["assets"]);

    await act(async () => {
      await result.current([]);
    });

    // A batch where every Asset was skipped still refreshes the list and the
    // App State, without removing anything locally.
    expect(queryClient.getQueryData(["assets"])).toEqual(before);
    expect(getAssets.mock.calls.length).toBeGreaterThan(1);
  });
});
