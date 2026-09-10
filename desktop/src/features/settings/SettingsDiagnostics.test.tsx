import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { MemeSortClient } from "../../api/tauri-client";
import type { AppState, PendingJob, RuntimeHealthResult } from "../../api/types";
import { importSnapshot } from "../import/import-test-fixtures";
import { resetRuntimeHealthForTesting } from "../runtime/runtimeHealthStore";

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

function pendingJobFixture(overrides: Partial<PendingJob> = {}): PendingJob {
  return {
    job_id: "123e4567-e89b-12d3-a456-426614174003",
    type: "embed_asset",
    asset_id: "123e4567-e89b-12d3-a456-426614174002",
    asset_path: "originals/indexed.png",
    recipe_id: "recipe-1",
    attempt_count: 0,
    created_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-09T00:00:00Z",
    ...overrides,
  };
}

function appStateSnapshot(options: { pendingJobs?: PendingJob[]; pendingCount?: number; paused?: boolean } = {}): AppState {
  const base = diagnosticsAppState();
  return {
    ...base,
    library_status: { ...base.library_status, job_counts: { pending: options.pendingCount ?? 1 } },
    worker_loop: { ...base.worker_loop, paused: options.paused ?? true },
    pending_jobs: options.pendingJobs ?? [pendingJobFixture()],
  };
}

function diagnosticsAppState(): AppState {
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
    library_status: {
      total_assets: 3,
      job_counts: { pending: 1 },
      recent_jobs: [
        {
          job_id: "123e4567-e89b-12d3-a456-426614174004",
          type: "embed_asset",
          status: "failed",
          asset_id: "123e4567-e89b-12d3-a456-426614174002",
          recipe_id: "recipe-1",
          attempt_count: 2,
          created_at: "2026-08-09T00:00:00Z",
          updated_at: "2026-08-09T01:00:00Z",
          error_code: "EmbeddingFailed",
          error_detail: "The embedding worker stopped.",
        },
      ],
    },
    worker_loop: {
      paused: true,
      running: true,
      event_log_path: "C:/Library/logs/worker-loop.jsonl",
      recent_events: [{ event: "worker-loop-paused", payload: {}, timestamp: 1754704800 }],
      persisted_events: [{ event: "tick-finished", payload: { processed_jobs: 1 }, timestamp: 1754704700 }],
    },
    import_task: importSnapshot(),
    pending_jobs: [pendingJobFixture()],
  };
}

function createClient(): MemeSortClient & Record<string, ReturnType<typeof vi.fn>> {
  const client = {
    getAppState: vi.fn(async (): Promise<AppState> => diagnosticsAppState()),
    getImportStatus: vi.fn(async () => importSnapshot()),
    getAssets: vi.fn(async () => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      assets: [],
    })),
    getAssetDetail: vi.fn(async () => {
      throw new Error("not under test");
    }),
    revealAsset: vi.fn(async () => undefined),
    openLogDirectory: vi.fn(async () => undefined),
    deleteAsset: vi.fn(async () => {
      throw new Error("must not delete Assets from diagnostics");
    }),
    removeSourceRecord: vi.fn(async () => {
      throw new Error("not under test");
    }),
    batchAssetAction: vi.fn(async () => {
      throw new Error("not under test");
    }),
    chooseImportFolder: vi.fn(async () => ({ selected_path: null })),
    chooseSearchImage: vi.fn(async () => ({ selected_path: null })),
    chooseLibraryFiles: vi.fn(async () => null),
    chooseLibraryFolder: vi.fn(async () => null),
    startLibraryImport: vi.fn(async () => {
      throw new Error("not under test");
    }),
    startImport: vi.fn(async () => importSnapshot()),
    startImportAndIndex: vi.fn(async () => importSnapshot()),
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
    getDuplicates: vi.fn(async () => {
      throw new Error("not under test");
    }),
    pauseWorkerLoop: vi.fn(async () => ({ running: true, paused: true })),
    resumeWorkerLoop: vi.fn(async () => ({ running: true, paused: false })),
    triggerWorkerLoop: vi.fn(async () => ({ running: true, paused: false })),
    runRuntimeHealthCheck: vi.fn(async () => healthyResult()),
    retryFailedJobs: vi.fn(async () => ({
      library_root: "C:/Library",
      retried_jobs: 2,
      failed_jobs_remaining: 0,
    })),
    getPendingJobs: vi.fn(async () => {
      throw new Error("must not issue an independent Pending Jobs read");
    }),
    deletePendingJobs: vi.fn(async (jobIds: string[]) => ({
      requested_job_ids: jobIds,
      deleted_job_ids: jobIds,
      skipped_job_ids: [],
    })),
    cancelSearch: vi.fn(async (requestId: string) => ({ request_id: requestId, cancelled: true, was_active: true })),
    copyAssetToClipboard: vi.fn(async () => undefined),
    copyOriginalFile: vi.fn(async () => undefined),
    copyOriginalFiles: vi.fn(async () => undefined),
    acceptDuplicatePair: vi.fn(async () => {
      throw new Error("not under test");
    }),
    clearAcceptedPairs: vi.fn(async () => {
      throw new Error("not under test");
    }),
  } as unknown as MemeSortClient & Record<string, ReturnType<typeof vi.fn>>;
  return client;
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

describe("Settings Runtime and installation (ticket 15)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("shows the read-only Runtime descriptor, health, Retry, and external setup-script guidance", async () => {
    const client = createClient();
    renderApp("/settings", client);

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runtime Descriptor" })).toBeInTheDocument();
    expect(screen.getByText("Qwen3-VL · 2048d · float32")).toBeInTheDocument();
    expect(screen.getByText("llama.cpp / Vulkan0; this descriptor is read-only.")).toBeInTheDocument();

    // Health authorizes indexing in this session; Retry remains available.
    await screen.findByText("Runtime ready in this app session", { exact: false });
    expect(screen.getByRole("button", { name: "Retry health check" })).toBeInTheDocument();

    // External installer guidance names the setup scripts and never offers an in-app installer.
    expect(screen.getByText("setup_windows_llama.ps1", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("setup_portable_runtime.bat", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /install runtime/i })).not.toBeInTheDocument();
  });

  it("reports failure with external setup-script instructions, not an in-app installer", async () => {
    const client = createClient();
    (client.runRuntimeHealthCheck as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...healthyResult(),
      smoke_test_ok: false,
      error: "Vulkan0 unavailable.",
    });
    renderApp("/settings", client);

    const failure = await screen.findByRole("alert", { name: "Runtime health failure" });
    expect(failure.textContent).toContain("Semantic search and indexing are unavailable");
    expect(failure.textContent).toContain("external setup script");
    expect(screen.getByText("setup_windows_llama.ps1", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /install runtime/i })).not.toBeInTheDocument();
  });
});

describe("Settings Advanced Diagnostics parity (ticket 15)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it.each([
    ["Worker Loop pause/resume/tick", "Worker Loop"],
    ["failed-Job retry", "Retry failed Jobs"],
    ["Pending Job inspect/delete", "Pending Jobs"],
    ["Recent Jobs", "Recent Jobs"],
    ["in-memory Worker events", "Worker events"],
    ["persisted worker log", "Persisted worker log"],
    ["open log directory", "Open log folder"],
  ])("covers legacy capability: %s", async (_label, headingOrButton) => {
    const client = createClient();
    renderApp("/settings", client);
    await screen.findByRole("heading", { name: "Settings" });
    expect(
      (await screen.findAllByText(headingOrButton, { exact: false })).length,
    ).toBeGreaterThan(0);
  });

  it("pauses, resumes, and ticks the Worker Loop through typed commands", async () => {
    const client = createClient();
    renderApp("/settings", client);
    await screen.findByRole("heading", { name: "Settings" });

    fireEvent.click(await screen.findByRole("button", { name: "Resume worker" }));
    expect(client.resumeWorkerLoop).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Worker Loop resumed.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run one tick" }));
    expect(client.triggerWorkerLoop).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Worker Loop tick requested.")).toBeInTheDocument();
  });

  it("retries failed Job records without changing Assets", async () => {
    const client = createClient();
    renderApp("/settings", client);

    fireEvent.click(await screen.findByRole("button", { name: "Retry failed Jobs" }));
    expect(client.retryFailedJobs).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Retried 2 failed Job record(s); 0 remain failed.")).toBeInTheDocument();
  });

  it("confirms deletion of selected Pending Job records without deleting Assets", async () => {
    const client = createClient();
    renderApp("/settings", client);

    fireEvent.click(await screen.findByLabelText("Select Pending Job embed_asset"));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected Pending Jobs" }));
    expect(screen.getByRole("alertdialog", { name: "Delete 1 Pending Job(s)?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete Pending Jobs" }));

    expect(client.deletePendingJobs).toHaveBeenCalledWith(["123e4567-e89b-12d3-a456-426614174003"]);
    expect(await screen.findByText("Deleted 1 Pending Job record(s); skipped 0.")).toBeInTheDocument();
    expect(client.deleteAsset).not.toHaveBeenCalled();
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("shows Recent Jobs and both Worker event sources from the read-only projection", async () => {
    const client = createClient();
    renderApp("/settings", client);

    expect(await screen.findByRole("heading", { name: "Recent Jobs" })).toBeInTheDocument();
    expect(screen.getByText("embed_asset · failed · attempt 2")).toBeInTheDocument();
    expect(screen.getByText("The embedding worker stopped.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Worker events" })).toBeInTheDocument();
    expect(screen.getByText("worker-loop-paused")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Persisted worker log" })).toBeInTheDocument();
    expect(screen.getByText("tick-finished")).toBeInTheDocument();
    expect(screen.getByText('{"processed_jobs":1}')).toBeInTheDocument();
  });

  it("opens the Library log directory through the native desktop command", async () => {
    const client = createClient();
    renderApp("/settings", client);

    fireEvent.click(await screen.findByRole("button", { name: "Open log folder" }));
    expect(client.openLogDirectory).toHaveBeenCalledTimes(1);
  });
});

describe("Advanced Diagnostics queue snapshot (ticket 02)", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("reads Pending Jobs from the app state snapshot without an independent Pending Jobs query", async () => {
    const client = createClient();
    renderApp("/settings", client);

    expect(await screen.findByLabelText("Select Pending Job embed_asset")).toBeInTheDocument();
    expect(screen.getByText("originals/indexed.png · recipe-1 · attempt 0")).toBeInTheDocument();
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("keeps the snapshot order and the full queue count when it exceeds the visible list", async () => {
    const client = createClient();
    (client.getAppState as ReturnType<typeof vi.fn>).mockResolvedValue(
      appStateSnapshot({
        pendingJobs: [
          pendingJobFixture({ job_id: "job-9", type: "dedupe_asset", asset_path: "originals/second.png", created_at: "2026-08-09T02:00:00Z" }),
          pendingJobFixture({ asset_path: "originals/first.png", created_at: "2026-08-09T01:00:00Z" }),
        ],
        pendingCount: 9,
      }),
    );
    renderApp("/settings", client);

    // The list renders in the snapshot order; no client-side sorting runs.
    const rows = await screen.findAllByLabelText(/^Select Pending Job /);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAccessibleName("Select Pending Job dedupe_asset");
    expect(rows[1]).toHaveAccessibleName("Select Pending Job embed_asset");
    // The count stays the full queue total, not the visible-list length.
    expect((await screen.findAllByText(/Indexing paused · 9 pending jobs/)).length).toBeGreaterThan(0);
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("refreshes the queue snapshot after a Worker action and updates the visible list", async () => {
    const refresh = deferred<AppState>();
    const client = createClient();
    (client.getAppState as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(appStateSnapshot())
      .mockReturnValue(refresh.promise);
    renderApp("/settings", client);

    fireEvent.click(await screen.findByRole("button", { name: "Resume worker" }));
    expect(await screen.findByText("Worker Loop resumed.")).toBeInTheDocument();
    expect(client.resumeWorkerLoop).toHaveBeenCalledTimes(1);
    // Command completion is not worker completion: the refreshed snapshot is
    // still pending, so the previous list stays visible.
    expect(refresh.settled).toBe(false);
    expect(screen.getByLabelText("Select Pending Job embed_asset")).toBeInTheDocument();

    await act(async () => {
      refresh.resolve(appStateSnapshot({ pendingJobs: [], pendingCount: 0, paused: false }));
    });
    expect(await screen.findByText("No Pending Jobs are waiting to be claimed.")).toBeInTheDocument();
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("refreshes the queue snapshot after a failed-Job retry so the retried Job re-enters the list", async () => {
    const refresh = deferred<AppState>();
    const client = createClient();
    (client.getAppState as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(appStateSnapshot({ pendingJobs: [], pendingCount: 0 }))
      .mockReturnValue(refresh.promise);
    renderApp("/settings", client);

    fireEvent.click(await screen.findByRole("button", { name: "Retry failed Jobs" }));
    expect(await screen.findByText("Retried 2 failed Job record(s); 0 remain failed.")).toBeInTheDocument();
    expect(screen.getByText("No Pending Jobs are waiting to be claimed.")).toBeInTheDocument();

    await act(async () => {
      refresh.resolve(appStateSnapshot({
        pendingJobs: [pendingJobFixture(), pendingJobFixture({ job_id: "job-4", type: "dedupe_asset", attempt_count: 1 })],
        pendingCount: 2,
      }));
    });
    expect(await screen.findByLabelText("Select Pending Job dedupe_asset")).toBeInTheDocument();
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("refreshes the queue snapshot after deleting Pending Jobs while the confirmation stays closed", async () => {
    const refresh = deferred<AppState>();
    const client = createClient();
    (client.getAppState as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(appStateSnapshot())
      .mockReturnValue(refresh.promise);
    renderApp("/settings", client);

    fireEvent.click(await screen.findByLabelText("Select Pending Job embed_asset"));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected Pending Jobs" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Pending Jobs" }));

    expect(await screen.findByText("Deleted 1 Pending Job record(s); skipped 0.")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(refresh.settled).toBe(false);
    expect(screen.getByLabelText("Select Pending Job embed_asset")).toBeInTheDocument();

    await act(async () => {
      refresh.resolve(appStateSnapshot({ pendingJobs: [], pendingCount: 0 }));
    });
    expect(await screen.findByText("No Pending Jobs are waiting to be claimed.")).toBeInTheDocument();
    // The refreshed count updates with the list: no stale pending-jobs label remains.
    expect(screen.queryByText(/pending jobs/)).not.toBeInTheDocument();
    expect(client.deletePendingJobs).toHaveBeenCalledTimes(1);
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("shows the global disconnect page when the refreshed snapshot fails", async () => {
    const client = createClient();
    (client.getAppState as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(appStateSnapshot())
      .mockRejectedValueOnce(new Error("refresh failed"));
    renderApp("/settings", client);

    fireEvent.click(await screen.findByRole("button", { name: "Resume worker" }));

    // Existing global interaction: a failed App State refresh disconnects the
    // shell; the stale diagnostics page is not kept on screen.
    await screen.findByText("MemeSort cannot reach its sidecar");
    expect(client.resumeWorkerLoop).toHaveBeenCalledTimes(1);
    expect(client.getPendingJobs).not.toHaveBeenCalled();
  });

  it("reports partially skipped deletions as deleted plus skipped without failing", async () => {
    const client = createClient();
    (client.deletePendingJobs as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      requested_job_ids: ["123e4567-e89b-12d3-a456-426614174003", "job-2"],
      deleted_job_ids: ["123e4567-e89b-12d3-a456-426614174003"],
      skipped_job_ids: ["job-2"],
    });
    renderApp("/settings", client);

    fireEvent.click(await screen.findByLabelText("Select Pending Job embed_asset"));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected Pending Jobs" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Pending Jobs" }));

    expect(await screen.findByText("Deleted 1 Pending Job record(s); skipped 1.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(client.deleteAsset).not.toHaveBeenCalled();
  });
});
