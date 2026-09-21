import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { MemeSortClient } from "../../api/tauri-client";
import type { AppState, AssetListResult, DuplicateScanResult } from "../../api/types";
import { importSnapshot } from "../import/import-test-fixtures";
import { resetRuntimeHealthForTesting, retryRuntimeHealthCheck } from "../runtime/runtimeHealthStore";

const LEFT_ID = "123e4567-e89b-12d3-a456-426614174000";
const RIGHT_ID = "123e4567-e89b-12d3-a456-426614174002";
const STALE_ID = "123e4567-e89b-12d3-a456-426614174099";

function assetsFixture(): AssetListResult {
  return {
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    assets: [
      {
        asset_id: LEFT_ID,
        library_path: "originals/left.png",
        library_url: "/media/originals/left.png",
        thumbnail_url: "/media/thumbnails/left.jpg",
        media_type: "image/png",
        content_hash: "hash-left",
        width: 640,
        height: 360,
        imported_at: "2026-08-09T00:00:00Z",
        updated_at: "2026-08-09T00:00:00Z",
        source_record_count: 3,
        source_records: [{ source_path: "C:/Source/left.png" }],
        status: "indexed",
      },
      {
        asset_id: RIGHT_ID,
        library_path: "originals/right.png",
        library_url: "/media/originals/right.png",
        thumbnail_url: "/media/thumbnails/right.jpg",
        media_type: "image/png",
        content_hash: "hash-right",
        width: 100,
        height: 100,
        imported_at: "2026-08-09T00:00:00Z",
        updated_at: "2026-08-09T00:00:00Z",
        source_record_count: 1,
        source_records: [{ source_path: "C:/Source/right.png" }],
        status: "indexed",
      },
    ],
  };
}

function pairsFixture(): DuplicateScanResult {
  return {
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    threshold: 0.92,
    pairs: [
      {
        score: 0.973,
        asset_a_id: LEFT_ID,
        asset_b_id: RIGHT_ID,
        asset_a_path: "originals/stale-left.png",
        asset_b_path: "originals/stale-right.png",
        asset_a_thumbnail_url: "/media/thumbnails/stale-left.jpg",
        asset_b_thumbnail_url: "/media/thumbnails/stale-right.jpg",
        asset_a_matched_source_ref: "frame:2",
        asset_b_matched_source_ref: "frame:7",
      },
    ],
  };
}

function failedHealthResult() {
  return {
    runtime_fingerprint: "runtime-1",
    backend_name: "llama.cpp",
    device: "Vulkan0",
    gpu_name: "Test GPU",
    gpu_vendor: "amd",
    gpu_vendor_id: "0x1002",
    text_smoke_vector_dim: 2048,
    image_smoke_vector_dim: 2048,
    diagnostic_steps: [{ step: "image-embedding-smoke", status: "failed", detail: "Vulkan0 unavailable." }],
    smoke_test_ok: false,
    error: "Vulkan0 unavailable.",
  };
}

function stalePairsFixture(): DuplicateScanResult {
  return {
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    threshold: 0.92,
    pairs: [
      {
        score: 0.96,
        asset_a_id: LEFT_ID,
        asset_b_id: STALE_ID,
        asset_a_path: "originals/left.png",
        asset_b_path: "originals/missing.png",
        asset_a_thumbnail_url: "/media/thumbnails/left.jpg",
        asset_b_thumbnail_url: "/media/thumbnails/missing.jpg",
        asset_a_matched_source_ref: "frame:1",
        asset_b_matched_source_ref: null,
      },
    ],
  };
}

function appStateFixture() {
  return {
    library_root: "C:/Library",
    runtime: { backend_name: "llama.cpp", device: "Vulkan0", model_label: "Qwen3-VL", output_dimension: 2048, storage_dtype: "float32" },
    setup_state: { health_check_ok: false },
    library_status: { total_assets: 2, job_counts: { pending: 0 } },
    worker_loop: { paused: false, running: true },
    import_task: importSnapshot(),
    pending_jobs: [],
  };
}

function deleteResult(assetId: string) {
  return {
    library_root: "C:/Library",
    asset_id: assetId,
    removed_source_path: null,
    asset_deleted: true,
    removed_source_records: 1,
    removed_jobs: 0,
    removed_renditions: 0,
    removed_embeddings: 0,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let settled = false;
  const promise = new Promise<T>((res) => {
    resolve = (value: T) => {
      settled = true;
      res(value);
    };
  });
  return { promise, resolve, get settled() { return settled; } };
}

function createClient(overrides: Partial<Record<keyof MemeSortClient, ReturnType<typeof vi.fn>>> = {}) {
  const client = {
    getAppState: vi.fn(async () => appStateFixture()),
    getImportStatus: vi.fn(async () => importSnapshot()),
    getAssets: vi.fn(async () => assetsFixture()),
    getAssetDetail: vi.fn(async () => {
      throw new Error("not under test");
    }),
    revealAsset: vi.fn(async () => undefined),
    openLogDirectory: vi.fn(async () => undefined),
    deleteAsset: vi.fn(async (assetId: string) => deleteResult(assetId)),
    removeSourceRecord: vi.fn(async () => {
      throw new Error("not under test");
    }),
    batchAssetAction: vi.fn(async () => {
      throw new Error("not under test");
    }),
    chooseSearchImage: vi.fn(async (requestId: string) => ({ request_id: requestId, selected_path: null })),
    chooseLibraryFiles: vi.fn(async () => null),
    chooseLibraryFolder: vi.fn(async () => null),
    startLibraryImport: vi.fn(async () => {
      throw new Error("not under test");
    }),
    pauseImport: vi.fn(async () => importSnapshot()),
    resumeImport: vi.fn(async () => importSnapshot()),
    searchText: vi.fn(async () => {
      throw new Error("not under test");
    }),
    searchImage: vi.fn(async () => {
      throw new Error("not under test");
    }),
    findSimilar: vi.fn(async () => {
      throw new Error("not under test");
    }),
    getDuplicates: vi.fn(async () => pairsFixture()),
    pauseWorkerLoop: vi.fn(async () => ({ running: true, paused: true })),
    resumeWorkerLoop: vi.fn(async () => ({ running: true, paused: false })),
    triggerWorkerLoop: vi.fn(async () => ({ running: true, paused: false })),
    runRuntimeHealthCheck: vi.fn(async () => ({
      runtime_fingerprint: "runtime-1",
      backend_name: "llama.cpp",
      device: "Vulkan0",
      gpu_name: "Test GPU",
      gpu_vendor: "amd",
      gpu_vendor_id: "0x1002",
      text_smoke_vector_dim: 2048,
      image_smoke_vector_dim: 2048,
      diagnostic_steps: [{ step: "image-embedding-smoke", status: "ok", detail: "ok" }],
      smoke_test_ok: true,
      error: null,
    })),
    retryFailedJobs: vi.fn(async () => ({ library_root: "C:/Library", retried_jobs: 0, failed_jobs_remaining: 0 })),
    getPendingJobs: vi.fn(async () => ({ jobs: [] })),
    deletePendingJobs: vi.fn(async (jobIds: string[]) => ({ requested_job_ids: jobIds, deleted_job_ids: jobIds, skipped_job_ids: [] })),
    cancelSearch: vi.fn(async (requestId: string) => ({ request_id: requestId, cancelled: true, was_active: true })),
    copyAssetToClipboard: vi.fn(async () => undefined),
    copyOriginalFile: vi.fn(async () => undefined),
    copyOriginalFiles: vi.fn(async () => undefined),
    acceptDuplicatePair: vi.fn(async (assetAId: string, assetBId: string) => ({
      library_root: "C:/Library",
      asset_a_id: assetAId,
      asset_b_id: assetBId,
      already_accepted: false,
    })),
    clearAcceptedPairs: vi.fn(async () => ({ library_root: "C:/Library", cleared_pairs: 2 })),
    ...overrides,
  } as unknown as MemeSortClient & Record<string, ReturnType<typeof vi.fn>>;
  return client;
}

function renderApp(route: string, client: MemeSortClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

async function scanDuplicates() {
  fireEvent.click(await screen.findByRole("button", { name: "Scan duplicates" }));
  await screen.findByRole("region", { name: "Duplicate pairs" });
}

describe("Duplicates redesigned workflow (ticket 16)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("shows comparison metadata from AssetSummary and DuplicatePair sources", async () => {
    const client = createClient();
    renderApp("/duplicates", client);

    await scanDuplicates();

    expect(client.getDuplicates).toHaveBeenCalledWith(0.92);
    expect(client.getAssets).toHaveBeenCalledTimes(1);
    const region = screen.getByRole("region", { name: "Duplicate pairs" });
    // Score and Matched Frames come from the DuplicatePair projection.
    expect(within(region).getByText("Similarity score 0.973")).toBeInTheDocument();
    expect(within(region).getByText("Matched frame: frame:2")).toBeInTheDocument();
    expect(within(region).getByText("Matched frame: frame:7")).toBeInTheDocument();
    // Dimensions and Source Record counts come from the AssetSummary map,
    // not the stale projected paths.
    expect(within(region).getByText("640 \u00D7 360 · 3 Source Records")).toBeInTheDocument();
    expect(within(region).getByText("100 \u00D7 100 · 1 Source Record")).toBeInTheDocument();
    expect(within(region).getByText("originals/left.png")).toBeInTheDocument();
    expect(within(region).getByText("originals/right.png")).toBeInTheDocument();
    expect(screen.queryByText("originals/stale-left.png")).not.toBeInTheDocument();
    // Large previews resolve through the managed Library Copy.
    const leftPreview = within(region).getByRole("img", { name: "originals/left.png preview" });
    expect(leftPreview).toHaveAttribute("src", "http://memesort-media.localhost/media/originals/left.png");
  });

  it("omits stale pairs with recoverable feedback instead of a broken comparison", async () => {
    const client = createClient({ getDuplicates: vi.fn(async () => stalePairsFixture()) });
    renderApp("/duplicates", client);

    fireEvent.click(await screen.findByRole("button", { name: "Scan duplicates" }));

    const stale = await screen.findByRole("status", { name: "Stale duplicate pairs" });
    expect(stale.textContent).toContain("1 duplicate pair");
    expect(stale.textContent).toContain("omitted");
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
    expect(await screen.findByText("No duplicate pairs found")).toBeInTheDocument();

    // Recoverable: rescanning refreshes the view.
    (client.getDuplicates as ReturnType<typeof vi.fn>).mockResolvedValueOnce(pairsFixture());
    fireEvent.click(within(stale).getByRole("button", { name: "Rescan duplicates" }));
    await screen.findByRole("region", { name: "Duplicate pairs" });
    expect(screen.getByText("originals/left.png")).toBeInTheDocument();
  });

  it("keeps both Assets through acceptDuplicatePair and removes the pair from the view", async () => {
    const client = createClient();
    renderApp("/duplicates", client);
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Both" }));
    expect(await screen.findByText(/Kept both originals\/left\.png and originals\/right\.png/)).toBeInTheDocument();
    expect(client.acceptDuplicatePair).toHaveBeenCalledWith(LEFT_ID, RIGHT_ID);
    // Resolved pair leaves the current view; exclusion persists on the next scan.
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
    expect(await screen.findByText("No duplicate pairs found")).toBeInTheDocument();
  });

  it("keeps Left with confirmation naming survivor, deleted Asset, and Source impact", async () => {
    const client = createClient();
    renderApp("/duplicates", client);
    await scanDuplicates();

    const keepLeft = screen.getByRole("button", { name: "Keep Left" });
    keepLeft.focus();
    fireEvent.click(keepLeft);
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    expect(dialog.textContent).toContain("originals/left.png");
    expect(dialog.textContent).toContain("originals/right.png");
    expect(dialog.textContent).toContain("1 Source Record");

    fireEvent.click(within(dialog).getByRole("button", { name: "Keep Left" }));
    expect(await screen.findByText(/Kept originals\/left\.png and deleted originals\/right\.png/)).toBeInTheDocument();
    expect(client.deleteAsset).toHaveBeenCalledWith(RIGHT_ID);
    expect(client.acceptDuplicatePair).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
    await waitFor(() => expect(document.querySelector("main.page")).toHaveFocus());
  });

  it("keeps Right with confirmation and deletes the left Asset via cascade", async () => {
    const client = createClient();
    renderApp("/duplicates", client);
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Right" }));
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/right\.png/i });
    expect(dialog.textContent).toContain("originals/right.png");
    expect(dialog.textContent).toContain("originals/left.png");
    expect(dialog.textContent).toContain("3 Source Records");

    fireEvent.click(within(dialog).getByRole("button", { name: "Keep Right" }));
    expect(await screen.findByText(/Kept originals\/right\.png and deleted originals\/left\.png/)).toBeInTheDocument();
    expect(client.deleteAsset).toHaveBeenCalledWith(LEFT_ID);
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
  });

  it("cancelling Keep confirmation leaves the pair visible without mutation", async () => {
    const client = createClient();
    renderApp("/duplicates", client);
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Left" }));
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("region", { name: "Duplicate pairs" })).toBeInTheDocument();
    expect(client.deleteAsset).not.toHaveBeenCalled();
    expect(client.acceptDuplicatePair).not.toHaveBeenCalled();
  });

  it("leaves the pair visible with retryable feedback when Keep Both fails", async () => {
    const client = createClient({
      acceptDuplicatePair: vi.fn(async () => {
        throw new Error("sidecar unavailable");
      }),
    });
    renderApp("/duplicates", client);
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Both" }));
    const failure = await screen.findByRole("alert", { name: /Action failed for originals\/left\.png/ });
    expect(failure.textContent).toContain("sidecar unavailable");
    // Pair stays visible for retry.
    expect(screen.getByRole("region", { name: "Duplicate pairs" })).toBeInTheDocument();

    (client.acceptDuplicatePair as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      library_root: "C:/Library",
      asset_a_id: LEFT_ID,
      asset_b_id: RIGHT_ID,
      already_accepted: false,
    });
    fireEvent.click(within(failure).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Kept both originals\/left\.png/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
  });

  it("leaves the pair visible with retryable feedback when Keep Left delete fails", async () => {
    const client = createClient({
      deleteAsset: vi.fn(async () => {
        throw new Error("delete failed");
      }),
    });
    renderApp("/duplicates", client);
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Left" }));
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep Left" }));

    const failure = await screen.findByRole("alert", { name: /Action failed for originals\/left\.png/ });
    expect(failure.textContent).toContain("delete failed");
    expect(screen.getByRole("region", { name: "Duplicate pairs" })).toBeInTheDocument();
    expect(client.deleteAsset).toHaveBeenCalledTimes(1);

    // The failed delete kept the pair; retrying re-confirms and succeeds.
    (client.deleteAsset as ReturnType<typeof vi.fn>).mockResolvedValueOnce(deleteResult(RIGHT_ID));
    fireEvent.click(within(failure).getByRole("button", { name: "Retry" }));
    const retryDialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    fireEvent.click(within(retryDialog).getByRole("button", { name: "Keep Left" }));
    expect(await screen.findByText(/Kept originals\/left\.png and deleted originals\/right\.png/)).toBeInTheDocument();
    expect(client.deleteAsset).toHaveBeenCalledTimes(2);
    expect(client.deleteAsset).toHaveBeenNthCalledWith(2, RIGHT_ID);
    expect(client.acceptDuplicatePair).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
  });

  it("retries a failed scan without losing the threshold", async () => {
    const client = createClient({
      getDuplicates: vi.fn(async () => {
        throw new Error("scan failed");
      }),
    });
    renderApp("/duplicates", client);

    fireEvent.click(await screen.findByRole("button", { name: "Scan duplicates" }));
    expect(await screen.findByText("scan failed")).toBeInTheDocument();

    (client.getDuplicates as ReturnType<typeof vi.fn>).mockResolvedValueOnce(pairsFixture());
    fireEvent.click(screen.getByRole("button", { name: "Retry scan" }));
    await screen.findByRole("region", { name: "Duplicate pairs" });
    expect(screen.getByText("originals/left.png")).toBeInTheDocument();
  });

  it("disables Rescan after a healthy scan loses current-session authorization", async () => {
    const client = createClient({ getDuplicates: vi.fn(async () => stalePairsFixture()) });
    renderApp("/duplicates", client);

    fireEvent.click(await screen.findByRole("button", { name: "Scan duplicates" }));
    await screen.findByRole("status", { name: "Stale duplicate pairs" });
    const rescan = screen.getByRole("button", { name: "Rescan duplicates" });
    expect(rescan).toBeEnabled();

    (client.runRuntimeHealthCheck as ReturnType<typeof vi.fn>).mockResolvedValueOnce(failedHealthResult());
    await act(async () => {
      await retryRuntimeHealthCheck(client);
    });

    await waitFor(() => {
      expect(rescan).toBeDisabled();
      expect(screen.getByRole("button", { name: "Scan duplicates" })).toBeDisabled();
    });
    fireEvent.click(rescan);
    expect(client.getDuplicates).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status", { name: "Stale duplicate pairs" })).toBeInTheDocument();
  });

  it("disables Retry scan after a healthy scan loses current-session authorization", async () => {
    const client = createClient({
      getDuplicates: vi.fn(async () => {
        throw new Error("scan failed");
      }),
    });
    renderApp("/duplicates", client);

    fireEvent.click(await screen.findByRole("button", { name: "Scan duplicates" }));
    const retry = await screen.findByRole("button", { name: "Retry scan" });
    expect(retry).toBeEnabled();

    (client.runRuntimeHealthCheck as ReturnType<typeof vi.fn>).mockResolvedValueOnce(failedHealthResult());
    await act(async () => {
      await retryRuntimeHealthCheck(client);
    });

    await waitFor(() => expect(retry).toBeDisabled());
    fireEvent.click(retry);
    expect(client.getDuplicates).toHaveBeenCalledTimes(1);
  });

  it("clears Accepted Pairs from Settings with confirmation without deleting Assets", async () => {
    const client = createClient();
    renderApp("/settings", client);

    expect(await screen.findByRole("heading", { name: "Accepted Duplicate Pairs" })).toBeInTheDocument();
    const clearAccepted = screen.getByRole("button", { name: "Clear accepted pairs" });
    clearAccepted.focus();
    fireEvent.click(clearAccepted);
    const dialog = await screen.findByRole("alertdialog", { name: "Clear all Accepted Duplicate Pairs?" });
    expect(dialog.textContent).toContain("without deleting Assets");
    fireEvent.click(within(dialog).getByRole("button", { name: "Clear accepted pairs" }));

    expect(await screen.findByText(/Cleared 2 Accepted Duplicate Pairs/)).toBeInTheDocument();
    expect(client.clearAcceptedPairs).toHaveBeenCalledTimes(1);
    expect(client.deleteAsset).not.toHaveBeenCalled();
    expect(client.batchAssetAction).not.toHaveBeenCalled();
    await waitFor(() => expect(clearAccepted).toHaveFocus());
  });

  it("shows retryable feedback when clearing Accepted Pairs fails", async () => {
    const client = createClient({
      clearAcceptedPairs: vi.fn(async () => {
        throw new Error("clear failed");
      }),
    });
    renderApp("/settings", client);

    fireEvent.click(await screen.findByRole("button", { name: "Clear accepted pairs" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Clear all Accepted Duplicate Pairs?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Clear accepted pairs" }));

    expect(await screen.findByText("clear failed")).toBeInTheDocument();
    expect(client.deleteAsset).not.toHaveBeenCalled();
  });

  it("permits cleared pairs to appear in a later scan", async () => {
    const client = createClient({
      getDuplicates: vi
        .fn()
        .mockResolvedValueOnce({ ...pairsFixture(), pairs: [] })
        .mockResolvedValueOnce(pairsFixture()),
    });
    renderApp("/duplicates", client);

    fireEvent.click(await screen.findByRole("button", { name: "Scan duplicates" }));
    expect(await screen.findByText("No duplicate pairs found")).toBeInTheDocument();

    // After Settings clear, the next scan may show the pair again.
    await client.clearAcceptedPairs();
    fireEvent.click(screen.getByRole("button", { name: "Scan duplicates" }));
    await screen.findByRole("region", { name: "Duplicate pairs" });
    expect(screen.getByText("originals/left.png")).toBeInTheDocument();
  });
});

describe("Duplicate Review deletion coordination (ticket 01)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("closes the confirmation, shows success, and evicts the shared cache before the refresh settles", async () => {
    const refresh = deferred<AppState>();
    const client = createClient({
      getAppState: vi.fn().mockResolvedValueOnce(appStateFixture()).mockReturnValue(refresh.promise),
    });
    const { queryClient } = renderApp("/duplicates", client);
    queryClient.setQueryData(["assets"], assetsFixture());
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Left" }));
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep Left" }));

    expect(await screen.findByText(/Kept originals\/left\.png and deleted originals\/right\.png/)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Duplicate pairs" })).not.toBeInTheDocument();
    expect(client.deleteAsset).toHaveBeenCalledTimes(1);
    expect(client.acceptDuplicatePair).not.toHaveBeenCalled();
    // The shared Asset list already dropped the deleted Asset while the
    // background refresh is still pending.
    expect((queryClient.getQueryData(["assets"]) as AssetListResult).assets.map((a) => a.asset_id)).toEqual([LEFT_ID]);
    expect((client.getAppState as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(refresh.settled).toBe(false);

    await act(async () => {
      refresh.resolve(appStateFixture());
    });
    await waitFor(() => {
      expect(refresh.settled).toBe(true);
    });
    expect((queryClient.getQueryData(["assets"]) as AssetListResult).assets.map((a) => a.asset_id)).toEqual([LEFT_ID]);
    expect(client.deleteAsset).toHaveBeenCalledTimes(1);
  });

  it("drops the inactive detail cache of the deleted Asset after coordination", async () => {
    const client = createClient();
    const { queryClient } = renderApp("/duplicates", client);
    queryClient.setQueryData(["asset-detail", RIGHT_ID], {
      library_root: "C:/Library",
      asset: assetsFixture().assets[1],
    });
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Left" }));
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep Left" }));

    expect(await screen.findByText(/Kept originals\/left\.png and deleted originals\/right\.png/)).toBeInTheDocument();
    await waitFor(() => {
      expect(queryClient.getQueryData(["asset-detail", RIGHT_ID])).toBeUndefined();
    });
  });

  it("never reports a failed background refresh as a delete failure", async () => {
    const client = createClient({
      getAppState: vi.fn().mockResolvedValueOnce(appStateFixture()).mockRejectedValueOnce(new Error("refresh failed")),
    });
    const { queryClient } = renderApp("/duplicates", client);
    queryClient.setQueryData(["assets"], assetsFixture());
    await scanDuplicates();

    fireEvent.click(screen.getByRole("button", { name: "Keep Left" }));
    const dialog = await screen.findByRole("alertdialog", { name: /keep originals\/left\.png/i });
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep Left" }));

    // Existing global interaction: a failed App State refresh disconnects the shell.
    await screen.findByText("MemeSort cannot reach its sidecar");
    expect(client.deleteAsset).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert", { name: /Action failed for originals\/left\.png/ })).not.toBeInTheDocument();
    expect((queryClient.getQueryData(["assets"]) as AssetListResult).assets.map((a) => a.asset_id)).toEqual([LEFT_ID]);
  });
});
