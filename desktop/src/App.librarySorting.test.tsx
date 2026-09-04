import { fireEvent, render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { App } from "./App";
import type { AssetDetail, AssetListResult } from "./api/types";
import { importSnapshot } from "./features/import/import-test-fixtures";
import { LIBRARY_PREFERENCE_KEYS } from "./features/library/libraryUrlState";
import { resetRuntimeHealthForTesting } from "./features/runtime/runtimeHealthStore";

const assets: AssetListResult = {
  library_root: "C:/Library",
  active_recipe_id: "recipe-1",
  active_recipe_label: "Vulkan0 recipe",
  assets: [
    {
      asset_id: "asset-newest",
      library_path: "originals/zebra.png",
      library_url: "/media/originals/zebra.png",
      thumbnail_url: "/media/thumbnails/zebra.jpg",
      media_type: "image/png",
      content_hash: "hash-new",
      width: 200,
      height: 100,
      imported_at: "2026-08-10T00:00:00Z",
      updated_at: "2026-08-10T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/zebra.png" }],
      status: "indexed",
    },
    {
      asset_id: "asset-oldest",
      library_path: "originals/apple.gif",
      library_url: "/media/originals/apple.gif",
      thumbnail_url: "/media/thumbnails/apple.jpg",
      media_type: "image/gif",
      content_hash: "hash-old",
      width: 320,
      height: 180,
      imported_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/apple.gif" }],
      status: "failed",
    },
    {
      asset_id: "asset-middle",
      library_path: "originals/mango.png",
      library_url: "/media/originals/mango.png",
      thumbnail_url: "/media/thumbnails/mango.jpg",
      media_type: "image/png",
      content_hash: "hash-mid",
      width: 160,
      height: 90,
      imported_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/mango.png" }],
      status: "pending",
    },
  ],
};

function detailFor(assetId: string): AssetDetail {
  const asset = assets.assets.find((a) => a.asset_id === assetId)!;
  return {
    ...asset,
    ocr_status: "ready",
    source_records: asset.source_records.map((s) => ({
      source_path: s.source_path,
      imported_at: asset.imported_at,
      last_seen_at: null,
    })),
    indexed_recipe_labels: [],
    stale_recipe_labels: [],
    ocr_results: [],
    renditions: [],
    jobs: [],
  };
}

function makeClient() {
  return {
    getAppState: async () => ({
      library_root: "C:/Library",
      runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
      setup_state: { health_check_ok: true },
      library_status: { total_assets: 3, job_counts: { pending: 1 } },
      worker_loop: { paused: false, running: true },
      import_task: importSnapshot(),
      pending_jobs: [],
    }),
    getImportStatus: async () => importSnapshot(),
    getAssets: async () => assets,
    getAssetDetail: async (assetId: string) => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      asset: detailFor(assetId),
    }),
    revealAsset: async () => undefined,
    openLogDirectory: async () => undefined,
    deleteAsset: async () => {
      throw new Error("not under test");
    },
    removeSourceRecord: async () => {
      throw new Error("not under test");
    },
    batchAssetAction: async () => {
      throw new Error("not under test");
    },
    chooseImportFolder: async () => {
      throw new Error("not under test");
    },
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
    startImport: async () => {
      throw new Error("not under test");
    },
    startImportAndIndex: async () => {
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
    findSimilar: async () => {
      throw new Error("not under test");
    },
    getDuplicates: async () => {
      throw new Error("not under test");
    },
    pauseWorkerLoop: async () => ({ running: true, paused: true }),
    resumeWorkerLoop: async () => ({ running: true, paused: false }),
    triggerWorkerLoop: async () => ({ running: true, paused: false }),
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
    retryFailedJobs: async () => {
      throw new Error("not under test");
    },
    getPendingJobs: async () => ({ jobs: [] }),
    deletePendingJobs: async () => {
      throw new Error("not under test");
    },
    cancelSearch: async (requestId: string) => ({ request_id: requestId, cancelled: true, was_active: true }),
    copyAssetToClipboard: async () => undefined,
    copyOriginalFile: async () => undefined,
    copyOriginalFiles: async () => undefined,
    acceptDuplicatePair: async () => {
      throw new Error("not under test");
    },
    clearAcceptedPairs: async () => {
      throw new Error("not under test");
    },
  };
}

function renderApp(route: string, client: ReturnType<typeof makeClient>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={client as never} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function cardOrder(): string[] {
  // Each card's open button is labelled `Open <display name>`.
  const buttons = screen.getAllByRole("button", { name: /^Open / });
  return buttons.map((button) => button.getAttribute("aria-label") ?? "");
}

describe("Library sorting, filtering, and density controls (ticket 08)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRuntimeHealthForTesting();
    vi.clearAllMocks();
  });

  it("renders every control in the Library toolbar with its active default value", async () => {
    const client = makeClient();
    renderApp("/", client);

    await screen.findByRole("button", { name: /Open zebra\.png/ });

    const sort = screen.getByLabelText("Sort") as HTMLSelectElement;
    const media = screen.getByLabelText("Media") as HTMLSelectElement;
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    const density = screen.getByLabelText("Density") as HTMLSelectElement;

    expect(sort.value).toBe("newest");
    expect(media.value).toBe("all");
    expect(status.value).toBe("all");
    expect(density.value).toBe("comfortable");

    // Default newest-first order: zebra (Aug) > mango (May) > apple (Jan).
    expect(cardOrder()).toEqual(["Open zebra.png", "Open mango.png", "Open apple.gif"]);
  });

  it("changing sort updates visible order through the URL contract", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /Open zebra\.png/ });
    expect(cardOrder()).toEqual(["Open zebra.png", "Open mango.png", "Open apple.gif"]);

    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "oldest" } });
    expect(cardOrder()).toEqual(["Open apple.gif", "Open mango.png", "Open zebra.png"]);
    expect((screen.getByLabelText("Sort") as HTMLSelectElement).value).toBe("oldest");

    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "name" } });
    // Name order: apple.gif < mango.png < zebra.png.
    expect(cardOrder()).toEqual(["Open apple.gif", "Open mango.png", "Open zebra.png"]);
  });

  it("filters compose (media AND status) and sort applies after filtering", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /Open zebra\.png/ });

    fireEvent.change(screen.getByLabelText("Media"), { target: { value: "gif" } });
    // Only the GIF remains.
    expect(cardOrder()).toEqual(["Open apple.gif"]);

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "indexed" } });
    // gif AND indexed matches nothing (the GIF is failed).
    expect(await screen.findByText("No Assets match these filters")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Open / })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    // gif AND failed matches the GIF again.
    expect(await screen.findByRole("button", { name: /Open apple\.gif/ })).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open apple.gif"]);
  });

  it("persists density without touching the URL and visibly changes the grid", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /Open zebra\.png/ });

    const grid = screen.getByRole("region", { name: "Assets" });
    expect(grid).toHaveAttribute("data-density", "comfortable");

    fireEvent.change(screen.getByLabelText("Density"), { target: { value: "compact" } });
    expect(screen.getByRole("region", { name: "Assets" })).toHaveAttribute("data-density", "compact");
    expect(localStorage.getItem(LIBRARY_PREFERENCE_KEYS.density)).toBe("compact");

    fireEvent.change(screen.getByLabelText("Density"), { target: { value: "comfortable" } });
    expect(screen.getByRole("region", { name: "Assets" })).toHaveAttribute("data-density", "comfortable");
    expect(localStorage.getItem(LIBRARY_PREFERENCE_KEYS.density)).toBe("comfortable");
  });

  it("empty filtered state explains how to clear filters and restores the wall", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /Open zebra\.png/ });

    fireEvent.change(screen.getByLabelText("Media"), { target: { value: "gif" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "indexed" } });

    expect(await screen.findByText("No Assets match these filters")).toBeInTheDocument();
    expect(
      screen.getByText(/Adjust the Media and Status filters, or clear them/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    // Both filters reset to all and the full newest-first wall returns.
    expect(await screen.findByRole("button", { name: /Open zebra\.png/ })).toBeInTheDocument();
    expect((screen.getByLabelText("Media") as HTMLSelectElement).value).toBe("all");
    expect((screen.getByLabelText("Status") as HTMLSelectElement).value).toBe("all");
    expect(cardOrder()).toEqual(["Open zebra.png", "Open mango.png", "Open apple.gif"]);
  });

  it("restores sort and filters from a deep link on first load", async () => {
    const client = makeClient();
    renderApp("/?sort=oldest&media=gif&status=failed", client);

    expect(await screen.findByRole("button", { name: /Open apple\.gif/ })).toBeInTheDocument();
    expect((screen.getByLabelText("Sort") as HTMLSelectElement).value).toBe("oldest");
    expect((screen.getByLabelText("Media") as HTMLSelectElement).value).toBe("gif");
    expect((screen.getByLabelText("Status") as HTMLSelectElement).value).toBe("failed");
    expect(cardOrder()).toEqual(["Open apple.gif"]);
  });

  it("URL-backed controls participate in back/forward navigation", async () => {
    const client = makeClient();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function BackForward() {
      const navigate = useNavigate();
      return (
        <div>
          <button type="button" onClick={() => navigate(-1)}>
            Go back
          </button>
          <button type="button" onClick={() => navigate(1)}>
            Go forward
          </button>
        </div>
      );
    }
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/", "/?sort=oldest"]} initialIndex={1}>
          <BackForward />
          <App client={client as never} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Start on the deep-linked oldest sort.
    await screen.findByRole("button", { name: /Open apple\.gif/ });
    expect((screen.getByLabelText("Sort") as HTMLSelectElement).value).toBe("oldest");
    expect(cardOrder()).toEqual(["Open apple.gif", "Open mango.png", "Open zebra.png"]);

    fireEvent.click(screen.getByRole("button", { name: "Go back" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    // Back to default newest.
    expect((screen.getByLabelText("Sort") as HTMLSelectElement).value).toBe("newest");
    expect(cardOrder()).toEqual(["Open zebra.png", "Open mango.png", "Open apple.gif"]);

    fireEvent.click(screen.getByRole("button", { name: "Go forward" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect((screen.getByLabelText("Sort") as HTMLSelectElement).value).toBe("oldest");
    expect(cardOrder()).toEqual(["Open apple.gif", "Open mango.png", "Open zebra.png"]);
  });
});
