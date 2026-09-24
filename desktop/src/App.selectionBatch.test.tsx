import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { App } from "./App";
import type { AssetDetail, AssetListResult } from "./api/types";
import { importSnapshot } from "./features/import/import-test-fixtures";

const FIRST_ASSET = "123e4567-e89b-12d3-a456-426614174000";
const SECOND_ASSET = "123e4567-e89b-12d3-a456-426614174001";
const THIRD_ASSET = "123e4567-e89b-12d3-a456-426614174002";

// Distinct imported_at so default `newest` sort yields a stable visual order:
// SECOND (08-10, newest) -> FIRST (08-09) -> THIRD (08-08, oldest).
const assets: AssetListResult = {
  library_root: "C:/Library",
  active_recipe_id: "recipe-1",
  active_recipe_label: "Vulkan0 recipe",
  assets: [
    {
      asset_id: FIRST_ASSET,
      library_path: "originals/first.gif",
      library_url: "/media/originals/first.gif",
      thumbnail_url: "/media/thumbnails/first.jpg",
      media_type: "image/gif",
      content_hash: "hash-1",
      width: 320,
      height: 180,
      imported_at: "2026-08-09T00:00:00Z",
      updated_at: "2026-08-09T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/first.gif" }],
      status: "indexed",
    },
    {
      asset_id: SECOND_ASSET,
      library_path: "originals/second.png",
      library_url: "/media/originals/second.png",
      thumbnail_url: "/media/thumbnails/second.jpg",
      media_type: "image/png",
      content_hash: "hash-2",
      width: 160,
      height: 90,
      imported_at: "2026-08-10T00:00:00Z",
      updated_at: "2026-08-10T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/second.png" }],
      status: "indexed",
    },
    {
      asset_id: THIRD_ASSET,
      library_path: "originals/third.png",
      library_url: "/media/originals/third.png",
      thumbnail_url: "/media/thumbnails/third.jpg",
      media_type: "image/png",
      content_hash: "hash-3",
      width: 120,
      height: 120,
      imported_at: "2026-08-08T00:00:00Z",
      updated_at: "2026-08-08T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/third.png" }],
      status: "indexed",
    },
  ],
};

function detailFor(assetId: string): AssetDetail {
  const base = assets.assets.find((a) => a.asset_id === assetId)!;
  return {
    ...base,
    ocr_status: "ready",
    source_records: base.source_records.map((s) => ({
      source_path: s.source_path,
      imported_at: base.imported_at,
      last_seen_at: null,
    })),
    indexed_recipe_labels: [],
    stale_recipe_labels: [],
    ocr_results: [],
    renditions: [],
    jobs: [],
  };
}

function healthyCheck() {
  return {
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
  };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    getAppState: async () => ({
      library_root: "C:/Library",
      runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
      library_status: { total_assets: 3, job_counts: { pending: 0 } },
      worker_loop: { paused: false, running: true },
      import_task: importSnapshot(),
      pending_jobs: [],
    }),
    getImportStatus: async () => importSnapshot(),
    getAssets: async () => assets,
    getAssetDetail: vi.fn(async (assetId: string) => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      asset: detailFor(assetId),
    })),
    revealAsset: vi.fn(async () => undefined),
    openLogDirectory: async () => undefined,
    deleteAsset: vi.fn(async (assetId: string) => ({
      library_root: "C:/Library",
      asset_id: assetId,
      removed_source_path: null,
      asset_deleted: true,
      removed_source_records: 1,
      removed_jobs: 0,
      removed_renditions: 0,
      removed_embeddings: 0,
    })),
    removeSourceRecord: vi.fn(async () => {
      throw new Error("not under test");
    }),
    batchAssetAction: vi.fn(
      async (action: "delete" | "rebuild-active-index", assetIds: string[]) => ({
        library_root: "C:/Library",
        action,
        requested_asset_ids: assetIds,
        affected_asset_ids: assetIds,
        skipped_running_asset_ids: [],
        removed_source_records: 0,
        removed_jobs: 0,
        removed_renditions: 0,
        removed_embeddings: 0,
        reindex_jobs_created: action === "rebuild-active-index" ? assetIds.length : 0,
      }),
    ),
    chooseSearchImage: async () => {
      throw new Error("not under test");
    },
    chooseLibraryFiles: async () => {
      throw new Error("not under test");
    },
    chooseLibraryFolder: async () => {
      throw new Error("not under test");
    },
    startLibraryImport: async () => {
      throw new Error("not under test");
    },
    pauseImport: async () => {
      throw new Error("not under test");
    },
    resumeImport: async () => {
      throw new Error("not under test");
    },
    searchText: async () => {
      throw new Error("not under test");
    },
    searchImage: async () => {
      throw new Error("not under test");
    },
    findSimilar: vi.fn(async () => {
      throw new Error("not under test");
    }),
    getDuplicates: async () => {
      throw new Error("not under test");
    },
    pauseWorkerLoop: async () => ({ running: true, paused: true }),
    resumeWorkerLoop: async () => ({ running: true, paused: false }),
    triggerWorkerLoop: async () => ({ running: true, paused: false }),
    runRuntimeHealthCheck: vi.fn(async () => healthyCheck()),
    retryFailedJobs: async () => {
      throw new Error("not under test");
    },
    deletePendingJobs: async () => {
      throw new Error("not under test");
    },
    cancelSearch: async (requestId: string) => ({
      request_id: requestId,
      cancelled: true,
      was_active: true,
    }),
    copyAssetToClipboard: vi.fn(async () => undefined),
    copyOriginalFile: vi.fn(async () => undefined),
    copyOriginalFiles: vi.fn(async () => undefined),
    acceptDuplicatePair: async () => {
      throw new Error("not under test");
    },
    clearAcceptedPairs: async () => {
      throw new Error("not under test");
    },
    ...overrides,
  };
}

function HistoryCapture({ onCapture }: { onCapture: (loc: { pathname: string; search: string }) => void }) {
  const location = useLocation();
  onCapture({ pathname: location.pathname, search: location.search });
  return null;
}

function renderApp(route: string | string[], client: ReturnType<typeof makeClient>, initialIndex?: number) {
  const entries = Array.isArray(route) ? route : [route];
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let testLocation = { pathname: "", search: "" };
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
        <HistoryCapture onCapture={(loc) => (testLocation = loc)} />
        <App client={client as never} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, getLocation: () => testLocation, queryClient };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function selectByLabel(label: string) {
  fireEvent.click(await screen.findByLabelText(label));
}

describe("Selection toolbar and batch actions (ticket 17)", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("copies one selection with the single-file method (ID-only) and preserves selection", async () => {
    const client = makeClient();
    renderApp("/", client);

    await selectByLabel("Select first.gif");
    fireEvent.click(await screen.findByRole("button", { name: "Copy original files" }));

    expect(await screen.findByText("Original file reference copied.")).toBeInTheDocument();
    expect(client.copyOriginalFile).toHaveBeenCalledTimes(1);
    expect(client.copyOriginalFile).toHaveBeenCalledWith(FIRST_ASSET);
    expect(client.copyOriginalFiles).not.toHaveBeenCalled();
    const [passed] = (client.copyOriginalFile as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(typeof passed).toBe("string");
    expect(passed).not.toContain("/");
    expect(passed).not.toContain("C:");

    // Selection preserved after successful Copy.
    expect((screen.getByLabelText("Select first.gif") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("toolbar", { name: "Selection toolbar" })).toBeInTheDocument();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("copies multiple selections once with stable visual-order IDs (ID-only, never paths)", async () => {
    const client = makeClient();
    renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    // Click in reverse visual order: visual newest-first is second -> first -> third.
    // Selecting third then second must still copy as [second, third].
    fireEvent.click(screen.getByLabelText("Select third.png"));
    fireEvent.click(screen.getByLabelText("Select second.png"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy original files" }));

    expect(await screen.findByText("Copied 2 original file references.")).toBeInTheDocument();
    expect(client.copyOriginalFiles).toHaveBeenCalledTimes(1);
    expect(client.copyOriginalFiles).toHaveBeenCalledWith([SECOND_ASSET, THIRD_ASSET]);
    expect(client.copyOriginalFile).not.toHaveBeenCalled();
    const [passedIds] = (client.copyOriginalFiles as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(Array.isArray(passedIds)).toBe(true);
    for (const id of passedIds as string[]) {
      expect(typeof id).toBe("string");
      expect(id).not.toContain("/");
      expect(id).not.toContain("C:");
    }

    // Selection preserved after multi Copy.
    expect((screen.getByLabelText("Select second.png") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Select third.png") as HTMLInputElement).checked).toBe(true);
  });

  it("shows copy failure without clearing selection and without promising clipboard restoration", async () => {
    const client = makeClient({
      copyOriginalFiles: vi.fn(async () => {
        throw { error: "SidecarError", detail: "Clipboard is busy.", retryable: true };
      }),
    });
    renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    fireEvent.click(screen.getByLabelText("Select first.gif"));
    fireEvent.click(screen.getByLabelText("Select second.png"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy original files" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Clipboard is busy.");
    expect(alert.textContent).not.toMatch(/rollback/i);
    expect(alert.textContent).not.toMatch(/restor/i);
    // Failed Copy preserves selection.
    expect((screen.getByLabelText("Select first.gif") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Select second.png") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    expect(client.copyOriginalFiles).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "failure"] as const)(
    "does not surface a delayed single-card copy %s after its target context changes",
    async (outcome) => {
      const gate = deferred<void>();
      const client = makeClient({
        copyAssetToClipboard: vi.fn(async () => gate.promise),
      });
      const { container } = renderApp("/", client);

      await screen.findByLabelText("Select first.gif");
      const card = container.querySelector<HTMLElement>(`article[data-asset-id="${FIRST_ASSET}"]`);
      expect(card).not.toBeNull();
      fireEvent.contextMenu(card as HTMLElement, { button: 2, clientX: 200, clientY: 150 });
      fireEvent.click(await screen.findByRole("menuitem", { name: "Copy image" }));
      expect(client.copyAssetToClipboard).toHaveBeenCalledWith(FIRST_ASSET);

      // A filter transition removes the copied card from the active wall and
      // invalidates the operation before its native promise settles.
      fireEvent.click(screen.getByRole("button", { name: "Stills" }));
      expect(screen.queryByLabelText("Select first.gif")).not.toBeInTheDocument();
      await act(async () => {
        if (outcome === "success") gate.resolve();
        else gate.reject({ error: "SidecarError", detail: "Asset A copy failed.", retryable: true });
        await Promise.resolve();
      });
      await flush();

      expect(screen.queryByText("Copied to clipboard. Paste into QQ or WeChat.")).not.toBeInTheDocument();
      expect(screen.queryByText("Asset A copy failed.")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Select second.png")).toBeInTheDocument();
    },
  );

  it.each(["success", "failure"] as const)(
    "does not surface a delayed batch copy %s after its selection changes",
    async (outcome) => {
      const gate = deferred<void>();
      const client = makeClient({
        copyOriginalFiles: vi.fn(async () => gate.promise),
      });
      renderApp("/", client);

      await selectByLabel("Select first.gif");
      await selectByLabel("Select second.png");
      fireEvent.click(screen.getByRole("button", { name: "Copy original files" }));
      expect(client.copyOriginalFiles).toHaveBeenCalledWith([SECOND_ASSET, FIRST_ASSET]);

      // Removing one selected ID changes the operation context even though
      // the remaining Asset is still present and browseable.
      fireEvent.click(screen.getByLabelText("Select second.png"));
      expect(screen.getByText("1 selected")).toBeInTheDocument();
      await act(async () => {
        if (outcome === "success") gate.resolve();
        else gate.reject({ error: "SidecarError", detail: "Batch copy failed.", retryable: true });
        await Promise.resolve();
      });
      await flush();

      expect(screen.queryByText("Copied 2 original file references.")).not.toBeInTheDocument();
      expect(screen.queryByText("Batch copy failed.")).not.toBeInTheDocument();
    },
  );

  it("clears selection explicitly via Clear selection", async () => {
    const client = makeClient();
    renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    fireEvent.click(screen.getByLabelText("Select first.gif"));
    fireEvent.click(screen.getByLabelText("Select second.png"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("toolbar", { name: "Selection toolbar" })).not.toBeInTheDocument();
    expect((screen.getByLabelText("Select first.gif") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText("Select second.png") as HTMLInputElement).checked).toBe(false);
    expect(client.copyOriginalFile).not.toHaveBeenCalled();
    expect(client.copyOriginalFiles).not.toHaveBeenCalled();
    expect(client.batchAssetAction).not.toHaveBeenCalled();
  });

  it("preserves selection after successful Rebuild and keeps Delete/Rebuild confirmations", async () => {
    const client = makeClient();
    renderApp("/", client);

    await selectByLabel("Select first.gif");

    const rebuild = screen.getByRole("button", { name: "Rebuild Active Index" });
    rebuild.focus();
    fireEvent.click(rebuild);
    const dialog = await screen.findByRole("alertdialog", { name: "Rebuild 1 selected Asset(s)?" });
    expect(dialog).toHaveTextContent("Running Asset jobs are skipped.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Queue rebuild" }));

    expect(await screen.findByText(/Queued 1 Active Index rebuild\(s\); skipped 0 running Asset\(s\)\./)).toBeInTheDocument();
    expect(client.batchAssetAction).toHaveBeenCalledWith("rebuild-active-index", [FIRST_ASSET]);
    await waitFor(() => expect(rebuild).toHaveFocus());
    // Rebuild preserves selection.
    expect((screen.getByLabelText("Select first.gif") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    // Delete confirmation remains.
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const deleteDialog = await screen.findByRole("alertdialog", { name: "Delete 1 selected Asset(s)?" });
    fireEvent.click(within(deleteDialog).getByRole("button", { name: "Cancel" }));
    expect(client.batchAssetAction).toHaveBeenCalledTimes(1);
  });

  it("rechecks authorization at Confirm time before rebuilding the Active Index", async () => {
    const runRuntimeHealthCheck = vi
      .fn()
      .mockResolvedValueOnce(healthyCheck())
      .mockResolvedValueOnce({ ...healthyCheck(), smoke_test_ok: false, error: "Vulkan0 authorization was lost." });
    const client = makeClient({ runRuntimeHealthCheck });
    const { queryClient } = renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    fireEvent.click(screen.getByLabelText("Select first.gif"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Rebuild Active Index" })).toBeEnabled());
    const rebuild = screen.getByRole("button", { name: "Rebuild Active Index" });
    rebuild.focus();
    fireEvent.click(rebuild);
    const dialog = await screen.findByRole("alertdialog", { name: "Rebuild 1 selected Asset(s)?" });

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["runtime-health"] });
    });
    await waitFor(() => expect(rebuild).toBeDisabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Queue rebuild" }));

    expect(client.batchAssetAction).not.toHaveBeenCalled();
    expect(await screen.findByText(
      "Indexing authorization was lost. Run the Runtime health check before rebuilding the Active Index.",
    )).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".page-library")).toHaveFocus());
  });

  it("reconciles Delete from the mutation response: removes only affected, retains skipped", async () => {
    let currentAssets = [...assets.assets];
    const client = makeClient({
      getAssets: async () => ({ ...assets, assets: [...currentAssets] }),
      batchAssetAction: vi.fn(async (action: string, assetIds: string[]) => {
        // Simulate partial success: only the first visual ID deleted, the other skipped.
        // Persist the deletion in the mocked Library so the post-mutation refetch
        // keeps the affected Asset removed (mirrors the real backend).
        currentAssets = currentAssets.filter((a) => a.asset_id !== SECOND_ASSET);
        return {
          library_root: "C:/Library",
          action,
          requested_asset_ids: assetIds,
          affected_asset_ids: [SECOND_ASSET],
          skipped_running_asset_ids: [THIRD_ASSET],
          removed_source_records: 1,
          removed_jobs: 0,
          removed_renditions: 0,
          removed_embeddings: 0,
          reindex_jobs_created: 0,
        };
      }),
    });
    renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    fireEvent.click(screen.getByLabelText("Select second.png"));
    fireEvent.click(screen.getByLabelText("Select third.png"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete 2 selected Asset(s)?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete selected Assets" }));

    expect(await screen.findByText("Deleted 1 Asset(s).")).toBeInTheDocument();
    // Skipped ID retained and still checked; affected ID removed from selection.
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    expect((screen.getByLabelText("Select third.png") as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByLabelText("Select second.png")).not.toBeInTheDocument();
  });

  it("shows the confirmed deletion before the refresh settles", async () => {
    let releaseRefresh!: (value: AssetListResult) => void;
    const gate = new Promise<AssetListResult>((resolve) => {
      releaseRefresh = resolve;
    });
    let refreshSettled = false;
    let initialLoad = true;
    const client = makeClient({
      getAssets: async () => {
        if (initialLoad) {
          initialLoad = false;
          return assets;
        }
        return gate.then((value) => {
          refreshSettled = true;
          return value;
        });
      },
    });
    renderApp("/", client);

    await selectByLabel("Select second.png");
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete 1 selected Asset(s)?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete selected Assets" }));

    // The backend confirmed the deletion, so the Asset is gone from the wall
    // while the refetch is still pending.
    await waitFor(() => {
      expect(screen.queryByLabelText("Select second.png")).not.toBeInTheDocument();
    });
    expect(refreshSettled).toBe(false);

    await act(async () => {
      releaseRefresh({ ...assets, assets: assets.assets.filter((asset) => asset.asset_id !== SECOND_ASSET) });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(refreshSettled).toBe(true);
    expect(screen.queryByLabelText("Select second.png")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Select first.gif")).toBeInTheDocument();
  });

  it("keeps the confirmed deletion when the following refresh fails", async () => {
    let calls = 0;
    const client = makeClient({
      getAssets: async () => {
        calls += 1;
        // The initial load succeeds; the refresh after the deletion fails.
        if (calls === 1) return assets;
        throw new Error("sidecar unavailable");
      },
    });
    const { queryClient } = renderApp("/", client);

    await selectByLabel("Select second.png");
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete 1 selected Asset(s)?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete selected Assets" }));

    // A failed refresh is not reported as a failed deletion, and it does not
    // put the deleted Asset back into the Library.
    await waitFor(() => {
      const cached = queryClient.getQueryData(["assets"]) as AssetListResult;
      expect(cached.assets.map((asset) => asset.asset_id)).not.toContain(SECOND_ASSET);
    });
    expect(screen.queryByText(/could not be completed/i)).not.toBeInTheDocument();
    expect(client.batchAssetAction).toHaveBeenCalledTimes(1);
  });

  it("closes the detail only for Assets the backend deleted and keeps a skipped Asset's detail", async () => {
    let currentAssets = [...assets.assets];
    const client = makeClient({
      getAssets: async () => ({ ...assets, assets: [...currentAssets] }),
      batchAssetAction: vi.fn(async (action: string, assetIds: string[]) => {
        currentAssets = currentAssets.filter((asset) => asset.asset_id !== SECOND_ASSET);
        return {
          library_root: "C:/Library",
          action,
          requested_asset_ids: assetIds,
          affected_asset_ids: [SECOND_ASSET],
          skipped_running_asset_ids: [THIRD_ASSET],
          removed_source_records: 1,
          removed_jobs: 0,
          removed_renditions: 0,
          removed_embeddings: 0,
          reindex_jobs_created: 0,
        };
      }),
    });

    const deletedDetail = renderApp(`/?asset=${SECOND_ASSET}`, client);
    await screen.findByRole("complementary", { name: "Inspector" });
    await selectByLabel("Select second.png");
    fireEvent.click(screen.getByLabelText("Select third.png"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete 2 selected Asset(s)?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete selected Assets" }));

    await waitFor(() => {
      expect(deletedDetail.getLocation().search).not.toContain("asset=");
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    });
    // Only the deleted Asset left the selection.
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    expect((screen.getByLabelText("Select third.png") as HTMLInputElement).checked).toBe(true);
    deletedDetail.unmount();

    // A skipped Asset keeps its detail open.
    const skippedDetail = renderApp(`/?asset=${THIRD_ASSET}`, client);
    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(await screen.findByLabelText("Select third.png"));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const skippedDialog = await screen.findByRole("alertdialog", { name: "Delete 1 selected Asset(s)?" });
    fireEvent.click(within(skippedDialog).getByRole("button", { name: "Delete selected Assets" }));

    await waitFor(() => {
      expect(skippedDetail.getLocation().search).toContain(`asset=${THIRD_ASSET}`);
    });
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(screen.getByLabelText("Select third.png")).toBeInTheDocument();
  });

  it("keeps the open detail and the selection when Rebuild succeeds", async () => {
    const client = makeClient();
    const { getLocation } = renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    await selectByLabel("Select first.gif");
    fireEvent.click(screen.getByRole("button", { name: "Rebuild Active Index" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Rebuild 1 selected Asset(s)?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Queue rebuild" }));

    expect(await screen.findByText(/Queued 1 Active Index rebuild\(s\)/)).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(getLocation().search).toContain(`asset=${FIRST_ASSET}`);
    expect((screen.getByLabelText("Select first.gif") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("never writes selection to the URL and never persists it across restart", async () => {
    const client = makeClient();
    const { getLocation } = renderApp("/", client);

    await selectByLabel("Select first.gif");
    fireEvent.click(screen.getByLabelText("Select second.png"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();

    const search = getLocation().search;
    expect(search).not.toContain(FIRST_ASSET);
    expect(search).not.toContain(SECOND_ASSET);
    expect(search).not.toContain("selection");
    expect(search).not.toContain("selected");

    // No persisted preference may contain a selected Asset ID.
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)!;
      const value = localStorage.getItem(key) ?? "";
      expect(value).not.toContain(FIRST_ASSET);
      expect(value).not.toContain(SECOND_ASSET);
    }
  });

  it("keeps inspector selection/deletion coherent with the toolbar", async () => {
    let currentAssets = [...assets.assets];
    const client = makeClient({
      getAssets: async () => ({ ...assets, assets: [...currentAssets] }),
    });
    (client.deleteAsset as ReturnType<typeof vi.fn>).mockImplementation(async (assetId: string) => {
      currentAssets = currentAssets.filter((a) => a.asset_id !== assetId);
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
    });
    const { getLocation } = renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    // Toolbar selection first.
    fireEvent.click(screen.getByLabelText("Select second.png"));
    expect(await screen.findByText("1 selected")).toBeInTheDocument();

    // Open the inspector for the selected Asset; waterfall stays mounted.
    fireEvent.click(await screen.findByRole("button", { name: /second\.png/i }));
    expect(await screen.findByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect((screen.getByLabelText("Select second.png") as HTMLInputElement).checked).toBe(true);

    // Inspector Clipboard Copy preserves the toolbar checkbox selection.
    fireEvent.click(await screen.findByRole("button", { name: "Copy image" }));
    expect(await screen.findByText("Copied to clipboard. Paste into QQ or WeChat.")).toBeInTheDocument();
    expect((screen.getByLabelText("Select second.png") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    // Inspector Delete removes the Asset, closes only `asset`, and prunes the toolbar selection.
    fireEvent.click(await screen.findByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this Asset?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Asset" }));

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(client.deleteAsset).toHaveBeenCalledWith(SECOND_ASSET);
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    expect(getLocation().search).not.toContain("asset=");
    expect(screen.queryByLabelText("Select second.png")).not.toBeInTheDocument();
    expect(screen.queryByRole("toolbar", { name: "Selection toolbar" })).not.toBeInTheDocument();
    // Untouched Assets remain browseable.
    expect(screen.getByLabelText("Select first.gif")).toBeInTheDocument();
  });

  it("keeps the Rebuild Active Index safeguard: disabled while indexing is blocked but Delete stays available", async () => {
    const client = makeClient({
      runRuntimeHealthCheck: vi.fn(async () => ({
        ...healthyCheck(),
        smoke_test_ok: false,
        error: "Vulkan0 unavailable.",
      })),
    });
    renderApp("/", client);

    await screen.findByLabelText("Select first.gif");
    fireEvent.click(screen.getByLabelText("Select first.gif"));
    const toolbar = await screen.findByRole("toolbar", { name: "Selection toolbar" });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(within(toolbar).getByRole("button", { name: "Rebuild Active Index" })).toBeDisabled();
    expect(within(toolbar).getByRole("button", { name: "Delete selected" })).toBeEnabled();
    expect(within(toolbar).getByRole("button", { name: "Copy original files" })).toBeEnabled();
    expect(await screen.findByText(/Indexing is unavailable until the current session passes the Runtime health check/)).toBeInTheDocument();
  });
});
