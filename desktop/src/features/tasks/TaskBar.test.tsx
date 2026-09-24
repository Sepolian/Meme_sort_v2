import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { MemeSortClient } from "../../api/tauri-client";
import type { AppState, ImportTask, RuntimeHealthResult } from "../../api/types";
import { importResultSummary, importSnapshot } from "../import/import-test-fixtures";
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

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
    chooseSearchImage: async (requestId: string) => ({ request_id: requestId, selected_path: null }),
    chooseLibraryFiles: async () => null,
    chooseLibraryFolder: async () => null,
    startLibraryImport: async () => {
      throw new Error("not under test");
    },
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
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

describe("task visibility summary", () => {
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

describe("Activity entry", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    currentImportStatus = importSnapshot();
    currentAppState = baseAppState();
  });

  it("keeps the workspace mounted while task visibility changes", async () => {
    const client = createClient();
    const { container, queryClient } = renderApp("/", client);

    await screen.findByRole("heading", { name: "Your library" });
    await waitFor(() => expect(container.querySelector(".topbar")).not.toBeInTheDocument());
    const workspaceMain = container.querySelector(".workspace-main");

    currentAppState = {
      ...baseAppState(),
      worker_loop: { paused: true, running: true },
      library_status: { total_assets: 3, job_counts: { pending: 2 } },
    };
    await queryClient.invalidateQueries({ queryKey: ["app-state"] });
    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" })).toBeInTheDocument());
    expect(container.querySelector(".workspace-main")).toBe(workspaceMain);

    currentAppState = baseAppState();
    await queryClient.invalidateQueries({ queryKey: ["app-state"] });
    await waitFor(() => expect(screen.queryByRole("region", { name: "Activity" })).not.toBeInTheDocument());
    expect(container.querySelector(".workspace-main")).toBe(workspaceMain);
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

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("Importing 2 of 8"));

    fireEvent.click(screen.getByRole("button", { name: "Collapse Activity" }));
    expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("Importing 2 of 8");
    // Details collapse when minimized; the header stays discoverable.
    expect(within(screen.getByRole("region", { name: "Activity" })).queryByRole("list")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand Activity" }));
    expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("Importing 2 of 8");
  });

  it("keeps failed import visible instead of auto-disappearing", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "failed",
      partial_result: importResultSummary({
        new_assets: 1,
        failure_count: 1,
        failure_details: [{
          stage: "processing",
          code: "decode_failed",
          source_name: "broken.gif",
          detail: "The image could not be decoded.",
        }],
      }),
    });
    const client = createClient();
    renderApp("/", client);

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("Import Batch failed"));
    const activity = screen.getByRole("region", { name: "Activity" });
    expect(within(activity).getByRole("region", { name: "Import Failure details" })).toHaveTextContent("broken.gif");
    expect(within(activity).getAllByRole("link", { name: "View Import Failure details" })).toHaveLength(1);
    expect(screen.queryByRole("status", { name: "Background tasks summary" })).not.toBeInTheDocument();

    fireEvent.click(within(activity).getByRole("button", { name: "Collapse Activity" }));
    const activityDetails = activity.querySelector<HTMLElement>("#activity-details");
    expect(activityDetails).not.toBeNull();
    expect(activityDetails).toHaveAttribute("hidden");
    expect(getComputedStyle(activityDetails!).display).toBe("none");
    expect(within(activity).queryByRole("region", { name: "Import Failure details" })).not.toBeInTheDocument();
    expect(within(activity).getAllByRole("link", { name: "View Import Failure details" })).toHaveLength(1);
    expect(within(activity).getByRole("button", { name: "Expand Activity" })).toBeInTheDocument();
    expect(activity).toHaveTextContent("Import Batch failed");

    fireEvent.click(within(activity).getByRole("link", { name: "View Import Failure details" }));
    await waitFor(() => {
      expect(within(activity).getByRole("region", { name: "Import Failure details" })).toBeInTheDocument();
      expect(activityDetails).not.toHaveAttribute("hidden");
      expect(getComputedStyle(activityDetails!).display).not.toBe("none");
    });
    expect(within(activity).getAllByRole("link", { name: "View Import Failure details" })).toHaveLength(1);
  });

  it("keeps a fatal Import Batch error visible when no partial result exists", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-fatal",
      status: "failed",
      error: { error: "import_batch_preflight_failed", detail: "The selected folder is unavailable." },
    });
    renderApp("/", createClient());

    const details = await screen.findByRole("region", { name: "Import Failure details" });
    expect(details).toHaveTextContent(
      "The selected folder is unavailable.",
    );
  });

  it("keeps cancellation semantics without inventing a file failure", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-cancelled",
      status: "cancelled",
      partial_result: importResultSummary({ new_assets: 2, failure_count: 0 }),
      error: { error: "ImportCancelledError", detail: "The application stopped the Import Batch on shutdown." },
    });
    renderApp("/", createClient());

    const notice = await screen.findByRole("region", { name: "Import Batch cancelled" });
    expect(within(notice).getByRole("status", { name: "Import Batch result" })).toHaveTextContent(
      "Committed Assets remain in the Library",
    );
    expect(screen.queryByRole("region", { name: "Import Failure details" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View Import Failure details" })).not.toBeInTheDocument();
  });

  it("shows indexing work while the worker is paused", async () => {
    currentAppState = {
      ...baseAppState(),
      worker_loop: { paused: true, running: true },
      library_status: { total_assets: 3, job_counts: { pending: 2 } },
    };
    const client = createClient();
    const { container } = renderApp("/", client);

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("Indexing paused"));
    const taskBar = screen.getByRole("region", { name: "Activity" });
    const workspaceMain = container.querySelector(".workspace-main");

    expect(taskBar).toBeInTheDocument();
    expect(workspaceMain).toContainElement(taskBar);

    fireEvent.click(screen.getByRole("button", { name: "Collapse Activity" }));
    expect(screen.getByRole("link", { name: "Open diagnostics" })).toHaveAttribute("href", "/settings");
    expect(within(taskBar).queryByRole("list")).not.toBeInTheDocument();
  });

  it("shows active indexing progress in the single Activity entry", async () => {
    currentAppState = {
      ...baseAppState(),
      library_status: { total_assets: 3, job_counts: { pending: 2, running: 1 } },
    };
    const client = createClient();
    renderApp("/", client);

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("1 running job"));
    const activity = screen.getByRole("region", { name: "Activity" });
    expect(activity).toHaveAttribute("data-tone", "active");
    expect(within(activity).getByRole("listitem")).toHaveTextContent("Indexing 1 running job · 2 pending jobs");
    expect(screen.getAllByRole("status", { name: "Indexing activity" })).toHaveLength(1);
    expect(screen.getByRole("status", { name: "Indexing activity" })).toHaveAttribute("aria-live", "polite");
  });

  it("keeps a running job visible when no jobs are pending", async () => {
    currentAppState = {
      ...baseAppState(),
      library_status: { total_assets: 3, job_counts: { pending: 0, running: 1, failed: 0 } },
    };
    const client = createClient();
    renderApp("/", client);

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("1 running job"));
    expect(screen.getByRole("status", { name: "Indexing activity" })).toHaveAttribute("aria-live", "polite");
  });

  it("uses the authoritative failed count when recent details are incomplete", async () => {
    currentAppState = {
      ...baseAppState(),
      library_status: {
        total_assets: 3,
        job_counts: { pending: 0, failed: 3 },
        recent_jobs: [{
          job_id: "job-1",
          type: "embed_asset",
          status: "failed",
          asset_id: "asset-1",
          recipe_id: "recipe-1",
          attempt_count: 1,
          created_at: "2026-08-09T00:00:00Z",
          updated_at: "2026-08-09T00:00:00Z",
          error_code: "EmbeddingFailed",
          error_detail: "Embedding failed.",
        }],
      },
    };
    const client = createClient();
    renderApp("/", client);

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("3 failed jobs need retry"));
    expect(screen.getByRole("alert", { name: "Indexing activity" })).toHaveAttribute("aria-live", "assertive");
    expect(screen.getByRole("link", { name: "Open diagnostics" })).toHaveAttribute("href", "/settings");
  });

  it("keeps indexing failures discoverable while pending work remains", async () => {
    currentAppState = {
      ...baseAppState(),
      library_status: {
        total_assets: 3,
        job_counts: { pending: 2, running: 1, failed: 1 },
        recent_jobs: [{
          job_id: "job-1",
          type: "embed_asset",
          status: "failed",
          asset_id: "asset-1",
          recipe_id: "recipe-1",
          attempt_count: 1,
          created_at: "2026-08-09T00:00:00Z",
          updated_at: "2026-08-09T00:00:00Z",
          error_code: "EmbeddingFailed",
          error_detail: "Embedding failed.",
        }],
      },
    };
    const client = createClient();
    renderApp("/", client);

    await waitFor(() => expect(screen.getByRole("region", { name: "Activity" }).textContent).toContain("2 pending jobs"));
    const activity = screen.getByRole("region", { name: "Activity" });
    expect(activity).toHaveTextContent("1 running job");
    expect(activity).toHaveTextContent("1 failed job needs retry");
    expect(activity).toHaveAttribute("data-tone", "attention");
  });

  it("keeps Runtime recovery visible when collapsed and diagnostics when expanded", async () => {
    const client = createClient({ runRuntimeHealthCheck: vi.fn(async () => failedResult()) });
    renderApp("/", client);

    const activity = await screen.findByRole("region", { name: "Activity" });
    await within(activity).findByRole("button", { name: "Retry health check" });
    expect(within(activity).getByRole("button", { name: "Retry health check" })).toBeVisible();

    fireEvent.click(within(activity).getByRole("button", { name: "Collapse Activity" }));
    expect(within(activity).getByRole("button", { name: "Retry health check" })).toBeVisible();
    expect(activity.querySelector("#activity-details")).toHaveAttribute("hidden");

    fireEvent.click(within(activity).getByRole("button", { name: "Expand Activity" }));
    expect(within(activity).getByRole("alert", { name: "Runtime health failure" })).toHaveTextContent("Vulkan0 unavailable.");
    expect(within(activity).getByText("image-embedding-smoke · ok · Image embedding passed.")).toBeInTheDocument();
  });

  it("shows Runtime failure in the task bar while keeping browsing usable and supporting Retry", async () => {
    const runRuntimeHealthCheck = vi.fn(async () => failedResult());
    const client = createClient({ runRuntimeHealthCheck });
    renderApp("/", client);

    const failure = await screen.findByRole("alert", { name: "Runtime health failure" });
    expect(failure.textContent).toContain("external setup script");

    const taskBar = await screen.findByRole("region", { name: "Activity" });
    expect(taskBar.textContent).toContain("Runtime needs attention");
    expect(screen.queryByRole("status", { name: "Background tasks summary" })).not.toBeInTheDocument();

    // Browsing stays usable while semantic work is blocked.
    expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();

    runRuntimeHealthCheck.mockResolvedValueOnce(healthyResult());
    fireEvent.click(screen.getByRole("button", { name: "Retry health check" }));
    await waitFor(() => expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Activity" })).not.toBeInTheDocument(),
    );
  });

  it("keeps a keyboard-focused Retry control mounted and restores workspace focus after success", async () => {
    const retryResult = deferred<RuntimeHealthResult>();
    const runRuntimeHealthCheck = vi.fn()
      .mockResolvedValueOnce(failedResult())
      .mockReturnValueOnce(retryResult.promise);
    const client = createClient({ runRuntimeHealthCheck });
    const { container } = renderApp("/", client);

    const activity = await screen.findByRole("region", { name: "Activity" });
    await within(activity).findByRole("button", { name: "Retry health check" });
    const retryButton = within(activity).getByRole("button", { name: "Retry health check" });
    retryButton.focus();
    fireEvent.click(retryButton);

    const retryingButton = await screen.findByRole("button", { name: "Retrying…" });
    expect(retryingButton).toBeDisabled();
    expect(activity).toHaveTextContent("Retrying Runtime health…");
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2);

    await act(async () => {
      retryResult.resolve(healthyResult());
      await retryResult.promise;
    });
    await waitFor(() => expect(screen.queryByRole("region", { name: "Activity" })).not.toBeInTheDocument());
    expect(container.querySelector(".library-content")).toHaveFocus();
  });

  it("restores focus to a failed Retry control and renders one canonical error", async () => {
    const retryResult = deferred<RuntimeHealthResult>();
    const runRuntimeHealthCheck = vi.fn()
      .mockResolvedValueOnce(failedResult())
      .mockReturnValueOnce(retryResult.promise);
    const client = createClient({ runRuntimeHealthCheck });
    renderApp("/", client);

    const activity = await screen.findByRole("region", { name: "Activity" });
    await within(activity).findByRole("button", { name: "Retry health check" });
    const retryButton = within(activity).getByRole("button", { name: "Retry health check" });
    retryButton.focus();
    fireEvent.click(retryButton);
    expect(await screen.findByRole("button", { name: "Retrying…" })).toBeDisabled();

    await act(async () => {
      retryResult.resolve(failedResult());
      await retryResult.promise;
    });
    const failedRetryButton = await screen.findByRole("button", { name: "Retry health check" });
    await waitFor(() => expect(failedRetryButton).toHaveFocus());

    const failure = screen.getByRole("alert", { name: "Runtime health failure" });
    expect(within(failure).getAllByText("Vulkan0 unavailable.", { exact: true })).toHaveLength(1);
    expect(within(activity).getByRole("link", { name: "Open diagnostics" })).toHaveAttribute("href", "/settings");
  });

  it("restores focus to the Duplicates page after a successful Runtime retry", async () => {
    const runRuntimeHealthCheck = vi.fn()
      .mockResolvedValueOnce(failedResult())
      .mockResolvedValueOnce(healthyResult());
    const client = createClient({ runRuntimeHealthCheck });
    renderApp("/duplicates", client);

    await screen.findByRole("heading", { name: "Duplicate assets" });
    const activity = await screen.findByRole("region", { name: "Activity" });
    const retryButton = within(activity).getByRole("button", { name: "Retry health check" });
    retryButton.focus();
    fireEvent.click(retryButton);

    await waitFor(() => expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Activity" })).not.toBeInTheDocument());
    expect(screen.getByRole("main")).toHaveFocus();
  });
});
