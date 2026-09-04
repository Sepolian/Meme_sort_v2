import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { MemeSortClient } from "../../api/tauri-client";
import type { AssetListResult, RuntimeHealthResult } from "../../api/types";
import { importSnapshot } from "../import/import-test-fixtures";
import { resetRuntimeHealthForTesting } from "./runtimeHealthStore";

function healthyResult(): RuntimeHealthResult {
  return {
    runtime_fingerprint: "runtime-1",
    backend_name: "llama.cpp",
    device: "Vulkan0",
    gpu_name: "Test GPU",
    gpu_vendor: "amd",
    gpu_vendor_id: "0x1002",
    text_smoke_vector_dim: 2048,
    image_smoke_vector_dim: 2048,
    diagnostic_steps: [{ step: "image-embedding-smoke", status: "ok", detail: "Image embedding passed." }],
    smoke_test_ok: true,
    error: null,
  };
}

function failedResult(): RuntimeHealthResult {
  return { ...healthyResult(), smoke_test_ok: false, error: "Vulkan0 unavailable." };
}

const assets: AssetListResult = {
  library_root: "C:/Library",
  active_recipe_id: "recipe-1",
  active_recipe_label: "Vulkan0 recipe",
  assets: [
    {
      asset_id: "123e4567-e89b-12d3-a456-426614174000",
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
  ],
};

function makeClient(overrides: Partial<MemeSortClient> = {}): MemeSortClient & { runRuntimeHealthCheck: ReturnType<typeof vi.fn> } {
  const client = {
    getAppState: async () => ({
      library_root: "C:/Library",
      runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
      setup_state: { health_check_ok: false },
      library_status: { total_assets: 1, job_counts: { pending: 0 } },
      worker_loop: { paused: false, running: true },
      import_task: importSnapshot(),
      pending_jobs: [],
    }),
    getImportStatus: async () => importSnapshot(),
    getAssets: async () => assets,
    getAssetDetail: async () => {
      throw new Error("not under test");
    },
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
    chooseImportFolder: async () => ({ selected_path: null }),
    chooseSearchImage: async () => ({ selected_path: null }),
    chooseLibraryFiles: async () => null,
    chooseLibraryFolder: async () => null,
    startLibraryImport: async () => {
      throw new Error("not under test");
    },
    startImport: async () => importSnapshot(),
    startImportAndIndex: async () => importSnapshot(),
    pauseImport: async () => importSnapshot(),
    resumeImport: async () => importSnapshot(),
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
    runRuntimeHealthCheck: vi.fn(async () => healthyResult()),
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
    ...overrides,
  } as unknown as MemeSortClient & { runRuntimeHealthCheck: ReturnType<typeof vi.fn> };
  return client;
}

function renderApp(route: string, client: MemeSortClient, strict = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(strict ? <React.StrictMode>{tree}</React.StrictMode> : tree);
}

describe("startup Runtime health-check lifecycle (ticket 14)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
  });

  it("runs exactly one automatic health check under StrictMode", async () => {
    const client = makeClient();
    renderApp("/", client, true);

    await waitFor(() => expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1));
    // Settles to healthy without further automatic calls on rerenders/polls.
    await screen.findByRole("heading", { name: "Your library" });
    expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
  });

  it("does not start another automatic check on route remounts", async () => {
    const client = makeClient();
    const view = renderApp("/", client);
    await waitFor(() => expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { name: "Your library" });

    fireEvent.click(screen.getByRole("link", { name: "Duplicates" }));
    await screen.findByRole("heading", { name: "Duplicate assets" });
    fireEvent.click(screen.getByRole("link", { name: "Library" }));
    await screen.findByRole("heading", { name: "Your library" });

    expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it("does not start another automatic check on app-state polling", async () => {
    const client = makeClient();
    renderApp("/", client);
    await waitFor(() => expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { name: "Your library" });

    // Simulate the 5s app-state polling loop refetching without touching health.
    await client.getAppState();
    await client.getAppState();

    expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
  });

  it("shows compact Preparing search state while checking", async () => {
    let resolve!: (value: RuntimeHealthResult) => void;
    const client = makeClient({
      runRuntimeHealthCheck: vi.fn(() => new Promise<RuntimeHealthResult>((r) => (resolve = r))),
    });
    renderApp("/", client);

    expect(await screen.findByRole("status", { name: "Runtime health" })).toHaveTextContent("Preparing search");
    resolve(healthyResult());
    await waitFor(() => expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1));
  });

  it("blocks semantic search and indexing on failure while browsing and import still work", async () => {
    const client = makeClient({ runRuntimeHealthCheck: vi.fn(async () => failedResult()) });
    const view = renderApp("/", client);

    const failure = await screen.findByRole("alert", { name: "Runtime health failure" });
    expect(failure.textContent).toContain("Semantic search and indexing are unavailable");
    expect(failure.textContent).toContain("Library browsing and import still work");
    expect(failure.textContent).toContain("external setup script");

    // Library browsing still works on another route with the same session.
    view.unmount();
    resetRuntimeHealthForTesting();
    const browsingClient = makeClient({ runRuntimeHealthCheck: vi.fn(async () => failedResult()) });
    renderApp("/", browsingClient);
    await screen.findByRole("heading", { name: "Your library" });
    // Import entry remains enabled; browsing content loads.
    expect(screen.getByRole("button", { name: "Import" })).toBeEnabled();
  });

  it("supports explicit Retry after failure and updates shared state", async () => {
    const runRuntimeHealthCheck = vi.fn(async () => failedResult());
    const client = makeClient({ runRuntimeHealthCheck });
    renderApp("/", client);

    await screen.findByRole("alert", { name: "Runtime health failure" });
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(1);

    runRuntimeHealthCheck.mockResolvedValueOnce(healthyResult());
    fireEvent.click(screen.getByRole("button", { name: "Retry health check" }));

    await waitFor(() => expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert", { name: "Runtime health failure" })).not.toBeInTheDocument());
  });

  it("distinguishes persisted informational health from current-session authorization", async () => {
    // Server claims persisted health ok, but the current session check fails:
    // semantic search must still be blocked.
    const persistedOkClient = makeClient({
      getAppState: async () => ({
        library_root: "C:/Library",
        runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
        setup_state: { health_check_ok: true },
        library_status: { total_assets: 1, job_counts: { pending: 0 } },
        worker_loop: { paused: false, running: true },
        import_task: importSnapshot(),
        pending_jobs: [],
      }),
      runRuntimeHealthCheck: vi.fn(async () => failedResult()),
    });
    const first = renderApp("/", persistedOkClient);
    await screen.findByRole("alert", { name: "Runtime health failure" });
    first.unmount();

    // Server has no session health, but the current session check passes:
    // semantic search must be authorized.
    resetRuntimeHealthForTesting();
    const sessionOkClient = makeClient({
      getAppState: async () => ({
        library_root: "C:/Library",
        runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
        setup_state: { health_check_ok: false },
        library_status: { total_assets: 1, job_counts: { pending: 0 } },
        worker_loop: { paused: false, running: true },
        import_task: importSnapshot(),
        pending_jobs: [],
      }),
      runRuntimeHealthCheck: vi.fn(async () => healthyResult()),
    });
    renderApp("/", sessionOkClient);
    await waitFor(() => expect(sessionOkClient.runRuntimeHealthCheck).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.queryByRole("alert", { name: "Runtime health failure" })).not.toBeInTheDocument(),
    );
  });
});
