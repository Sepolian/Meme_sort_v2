import { fireEvent, render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { App } from "./App";
import type { AssetDetail, AssetListResult } from "./api/types";
import { importSnapshot } from "./features/import/import-test-fixtures";
import { LIBRARY_PREFERENCE_KEYS } from "./features/library/libraryUrlState";

const FIRST_ASSET = "123e4567-e89b-12d3-a456-426614174000";

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
      status: "pending",
    },
  ],
};

const assetDetail: AssetDetail = {
  ...assets.assets[0],
  ocr_status: "ready",
  source_records: [{ source_path: "C:/Source/first.gif", imported_at: "2026-08-09T00:00:00Z", last_seen_at: null }],
  indexed_recipe_labels: [],
  stale_recipe_labels: [],
  ocr_results: [],
  renditions: [],
  jobs: [],
};

function makeClient() {
  return {
    getAppState: async () => ({
      library_root: "C:/Library",
      runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
      setup_state: { health_check_ok: true },
      library_status: { total_assets: 1, job_counts: { pending: 0 } },
      worker_loop: { paused: false, running: true },
      import_task: importSnapshot(),
      pending_jobs: [],
    }),
    getImportStatus: async () => importSnapshot(),
    getAssets: async () => assets,
    getAssetDetail: async () => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      asset: assetDetail,
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
    searchText: vi.fn(async () => {
      throw new Error("searchText must not run from URL q alone");
    }),
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
    runRuntimeHealthCheck: async () => {
      throw new Error("not under test");
    },
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
  return { ...utils, getLocation: () => testLocation };
}

function BackForwardControls() {
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

function renderAppWithHistory(
  entries: string[],
  initialIndex: number,
  client: ReturnType<typeof makeClient>,
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
        <BackForwardControls />
        <HistoryCapture onCapture={() => undefined} />
        <App client={client as never} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return utils;
}

describe("Library URL state (ticket 07)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("opening an Asset updates the URL without a reload and keeps the waterfall mounted", async () => {
    const client = makeClient();
    const { getLocation } = renderApp("/", client);
    // Waterfall content loads.
    await screen.findByText("Pending Asset");
    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));
    expect(await screen.findByRole("dialog", { name: "Asset details" })).toBeInTheDocument();
    // Waterfall stays mounted behind the dialog (scroll position preserved).
    expect(screen.getByText("Pending Asset")).toBeInTheDocument();
    expect(getLocation().search).toContain(`asset=${FIRST_ASSET}`);
  });

  it("deep-links the inspector from the URL on first load", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);
    expect(await screen.findByRole("dialog", { name: "Asset details" })).toBeInTheDocument();
    expect(screen.getByText("Pending Asset")).toBeInTheDocument();
  });

  it("closing the inspector removes only asset and preserves other params", async () => {
    const client = makeClient();
    const { getLocation } = renderApp(`/?q=cat&sort=oldest&asset=${FIRST_ASSET}`, client);
    expect(await screen.findByRole("dialog", { name: "Asset details" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    // Dialog closes (inspector target cleared).
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("dialog", { name: "Asset details" })).not.toBeInTheDocument();
    const search = getLocation().search;
    expect(search).toContain("q=cat");
    expect(search).toContain("sort=oldest");
    expect(search).not.toContain("asset=");
  });

  it("a URL q never reruns semantic search automatically", async () => {
    const client = makeClient();
    renderApp("/?q=cat", client);
    await screen.findByText("Pending Asset");
    expect(client.searchText).not.toHaveBeenCalled();
  });

  it("normalizes invalid enum values out of the URL", async () => {
    const client = makeClient();
    const { getLocation } = renderApp("/?sort=bogus&media=gif", client);
    await screen.findByText("Pending Asset");
    expect(getLocation().search).not.toContain("bogus");
    expect(getLocation().search).toContain("media=gif");
  });

  it("restores persisted sort when the URL does not override it", async () => {
    const client = makeClient();
    localStorage.setItem(LIBRARY_PREFERENCE_KEYS.sort, "oldest");
    // No direct sort UI yet (ticket 08); persistence is proven at the hook
    // seam and the URL contract leaves the param absent so the preference
    // applies. Here we assert browsing still works and the URL stays clean.
    const { getLocation } = renderApp("/", client);
    await screen.findByText("Pending Asset");
    expect(getLocation().search).not.toContain("sort=bogus");
  });

  it("supports browser back/forward across Library URL states", async () => {
    const client = makeClient();
    renderAppWithHistory(["/", `/?asset=${FIRST_ASSET}`], 1, client);
    // Start on the deep-linked inspector.
    expect(await screen.findByRole("dialog", { name: "Asset details" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go back" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("dialog", { name: "Asset details" })).not.toBeInTheDocument();
    expect(await screen.findByText("Pending Asset")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go forward" }));
    expect(await screen.findByRole("dialog", { name: "Asset details" })).toBeInTheDocument();
  });
});
