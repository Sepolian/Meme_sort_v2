import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { MemeSortClient } from "../../api/tauri-client";
import type { AppState, ImportTask, RuntimeHealthResult } from "../../api/types";
import { importResultSummary, importSnapshot } from "../import/import-test-fixtures";
import { resetRuntimeHealthForTesting } from "../runtime/runtimeHealthStore";
import { summarizeTasks } from "./taskVisibility";

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

let currentImportStatus: ImportTask;
let currentAppState: AppState;

function baseAppState(): AppState {
  return {
    library_root: "C:/Library",
    runtime: {
      backend_name: "llama.cpp",
      device: "Vulkan0",
      model_label: "Qwen3-VL",
      output_dimension: 2048,
      storage_dtype: "float32",
    },
    setup_state: { health_check_ok: false },
    library_status: { total_assets: 1, job_counts: { pending: 0 } },
    worker_loop: { paused: false, running: true },
    import_task: importSnapshot(),
    pending_jobs: [],
  };
}

function createClient(overrides: Partial<MemeSortClient> = {}): MemeSortClient {
  return {
    getAppState: vi.fn(async (): Promise<AppState> => ({ ...currentAppState })),
    getImportStatus: vi.fn(async (): Promise<ImportTask> => currentImportStatus),
    getAssets: vi.fn(async () => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      assets: [],
    })),
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
    pauseImport: async () => currentImportStatus,
    resumeImport: async () => currentImportStatus,
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
  } as unknown as MemeSortClient;
}

function renderApp(route: string, client: MemeSortClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("task visibility summary (ticket 15)", () => {
  it("hides when idle, healthy, and non-actionable", () => {
    const summary = summarizeTasks({
      importTask: importSnapshot(),
      healthStatus: "healthy",
      healthBlocked: false,
      appState: baseAppState(),
    });
    expect(summary.visible).toBe(false);
    expect(summary.compactLabel).toBeNull();
  });

  it("shows completed success as idle without attention", () => {
    const summary = summarizeTasks({
      importTask: importSnapshot({ batch_id: "b1", status: "completed", result: importResultSummary() }),
      healthStatus: "healthy",
      healthBlocked: false,
      appState: baseAppState(),
    });
    expect(summary.visible).toBe(false);
  });

  it("keeps failed import discoverable as attention", () => {
    const summary = summarizeTasks({
      importTask: importSnapshot({ batch_id: "b1", status: "failed", partial_result: importResultSummary() }),
      healthStatus: "healthy",
      healthBlocked: false,
      appState: baseAppState(),
    });
    expect(summary.visible).toBe(true);
    expect(summary.attention).toBe(true);
    expect(summary.compactLabel).toContain("Import Batch failed");
  });
});

describe("compact top-bar entry and bottom task bar (ticket 15)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
    window.localStorage.clear();
    vi.clearAllMocks();
    currentImportStatus = importSnapshot();
    currentAppState = baseAppState();
  });

  it("hides both surfaces when idle and non-actionable", async () => {
    const client = createClient();
    renderApp("/", client);

    await screen.findByRole("heading", { name: "Your library" });
    await waitFor(() => expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1));

    expect(screen.queryByRole("status", { name: "Background tasks summary" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Background tasks" })).not.toBeInTheDocument();
  });

  it("shows agreeing compact and expanded state while importing", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
      supported_files: 12,
      processed_files: 4,
      current_source_name: "cat.gif",
    });
    const client = createClient();
    renderApp("/", client);

    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Background tasks summary" }).textContent).toContain(
        "Importing 4 of 12",
      ),
    );
    const topEntry = screen.getByRole("status", { name: "Background tasks summary" });
    expect(topEntry.textContent).toContain("Importing 4 of 12");
    const taskBar = screen.getByRole("region", { name: "Background tasks" });
    expect(taskBar.textContent).toContain("Importing 4 of 12");
    // Expanded details break the same state down without duplicating action controls.
    expect(taskBar.textContent).toContain("Import");
    expect(within(taskBar).queryByRole("button", { name: "Pause Import Batch" })).not.toBeInTheDocument();
    expect(within(taskBar).queryByRole("button", { name: "Resume Import Batch" })).not.toBeInTheDocument();
  });

  it("minimizes and expands without losing the compact header", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
      supported_files: 8,
      processed_files: 2,
    });
    const client = createClient();
    renderApp("/", client);

    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Background tasks" }).textContent).toContain("Importing 2 of 8"),
    );
    const taskBar = screen.getByRole("region", { name: "Background tasks" });
    expect(taskBar.textContent).toContain("Importing 2 of 8");

    fireEvent.click(screen.getByRole("button", { name: "Minimize tasks" }));
    expect(screen.getByRole("region", { name: "Background tasks" }).textContent).toContain("Importing 2 of 8");
    // Details collapse when minimized; the header stays discoverable.
    expect(screen.queryByText("Runtime health")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand tasks" }));
    expect(screen.getByRole("region", { name: "Background tasks" }).textContent).toContain("Importing 2 of 8");
  });

  it("keeps failed import visible instead of auto-disappearing", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "failed",
      partial_result: importResultSummary({ new_assets: 0, duplicate_assets: 0 }),
    });
    const client = createClient();
    renderApp("/", client);

    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Background tasks summary" }).textContent).toContain(
        "Import Batch failed",
      ),
    );
    const topEntry = screen.getByRole("status", { name: "Background tasks summary" });
    expect(topEntry.textContent).toContain("Import Batch failed");
    expect(screen.getByRole("region", { name: "Background tasks" })).toBeInTheDocument();
  });

  it("shows indexing work while the worker is paused", async () => {
    currentAppState = {
      ...baseAppState(),
      worker_loop: { paused: true, running: true },
      library_status: { total_assets: 3, job_counts: { pending: 2 } },
    };
    const client = createClient();
    renderApp("/", client);

    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Background tasks summary" }).textContent).toContain(
        "Indexing paused",
      ),
    );
    const topEntry = screen.getByRole("status", { name: "Background tasks summary" });
    expect(topEntry.textContent).toContain("Indexing paused");
    expect(screen.getByRole("region", { name: "Background tasks" })).toBeInTheDocument();
  });

  it("shows Runtime failure in the task bar while keeping browsing usable and supporting Retry", async () => {
    const runRuntimeHealthCheck = vi.fn(async () => failedResult());
    const client = createClient({ runRuntimeHealthCheck });
    renderApp("/", client);

    const failure = await screen.findByRole("alert", { name: "Runtime health failure" });
    expect(failure.textContent).toContain("external setup script");

    const topEntry = await screen.findByRole("status", { name: "Background tasks summary" });
    expect(topEntry.textContent).toContain("Runtime needs attention");
    expect(await screen.findByRole("region", { name: "Background tasks" })).toBeInTheDocument();

    // Browsing stays usable while semantic work is blocked.
    expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();

    runRuntimeHealthCheck.mockResolvedValueOnce(healthyResult());
    fireEvent.click(screen.getByRole("button", { name: "Retry health check" }));
    await waitFor(() => expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole("status", { name: "Background tasks summary" })).not.toBeInTheDocument(),
    );
  });
});
