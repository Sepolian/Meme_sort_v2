import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { MemeSortClient } from "../../api/tauri-client";
import type {
  AppState,
  AssetDetail,
  AssetDetailResult,
  AssetListResult,
  ImportTask,
} from "../../api/types";
import { importResultSummary, importSnapshot } from "./import-test-fixtures";

let currentImportStatus: ImportTask;
const getImportStatus = vi.fn(async (): Promise<ImportTask> => currentImportStatus);

const assets: AssetListResult = {
  library_root: "C:/Library",
  active_recipe_id: "recipe-1",
  active_recipe_label: "Vulkan0 recipe",
  assets: [
    {
      asset_id: "123e4567-e89b-12d3-a456-426614174000",
      library_path: "originals/first.gif",
      library_url: "/media/originals/first.gif",
      thumbnail_url: null,
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
  source_records: [
    { source_path: "C:/Source/first.gif", imported_at: "2026-08-09T00:00:00Z", last_seen_at: null },
  ],
  indexed_recipe_labels: [],
  stale_recipe_labels: [],
  ocr_results: [],
  renditions: [],
  jobs: [],
};

const appState: AppState = {
  library_root: "C:/Library",
  runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
  setup_state: { health_check_ok: true },
  library_status: { total_assets: 1, job_counts: { pending: 0 } },
  worker_loop: { paused: false, running: true },
  import_task: importSnapshot(),
  pending_jobs: [],
};

function createClient(overrides: Partial<MemeSortClient> = {}): MemeSortClient {
  return {
    getAppState: vi.fn(async (): Promise<AppState> => ({ ...appState, import_task: currentImportStatus })),
    getImportStatus,
    getAssets: vi.fn(async () => assets),
    getAssetDetail: vi.fn(async (): Promise<AssetDetailResult> => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      asset: assetDetail,
    })),
    revealAsset: unsupported,
    openLogDirectory: unsupported,
    deleteAsset: unsupported,
    removeSourceRecord: unsupported,
    batchAssetAction: unsupported,
    chooseImportFolder: unsupported,
    chooseSearchImage: unsupported,
    chooseLibraryFiles: vi.fn(async () => null),
    chooseLibraryFolder: vi.fn(async () => null),
    startLibraryImport: unsupported,
    startImport: unsupported,
    startImportAndIndex: unsupported,
    pauseImport: vi.fn(async () => currentImportStatus),
    resumeImport: vi.fn(async () => currentImportStatus),
    searchText: unsupported,
    searchImage: unsupported,
    findSimilar: unsupported,
    getDuplicates: unsupported,
    pauseWorkerLoop: unsupported,
    resumeWorkerLoop: unsupported,
    triggerWorkerLoop: unsupported,
    runRuntimeHealthCheck: unsupported,
    retryFailedJobs: unsupported,
    getPendingJobs: unsupported,
    deletePendingJobs: unsupported,
    cancelSearch: unsupported,
    ...overrides,
  } as MemeSortClient;
}

async function unsupported(): Promise<never> {
  throw new Error("Not used by this test.");
}

function renderApp(client: MemeSortClient) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { invalidateSpy, ...view };
}

function progressRegion() {
  return screen.getByRole("status", { name: "Import Batch progress" });
}

function invalidateCalls(
  spy: { mock: { calls: Array<Array<unknown>> } },
  key: string,
) {
  return spy.mock.calls.filter(([options]) => {
    const queryKey = (options as { queryKey?: readonly unknown[] } | undefined)?.queryKey;
    return Array.isArray(queryKey) && queryKey[0] === key;
  }).length;
}

describe("application-level Import Batch observer", () => {
  it("polls Import Batch status slowly while idle and quickly only while work is active", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    try {
      currentImportStatus = importSnapshot();
      const client = createClient();
      renderApp(client);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(getImportStatus).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(getImportStatus).toHaveBeenCalledTimes(2);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4_000);
      });
      expect(getImportStatus).toHaveBeenCalledTimes(2);

      currentImportStatus = importSnapshot({
        batch_id: "batch-1",
        status: "importing",
        running: true,
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(getImportStatus).toHaveBeenCalledTimes(3);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(800);
      });
      expect(getImportStatus).toHaveBeenCalledTimes(3);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
      });
      expect(getImportStatus).toHaveBeenCalledTimes(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps observing and invalidates Asset lists, App State, and Asset details exactly once when the batch finishes on another page", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
    });
    const client = createClient();
    const { invalidateSpy } = renderApp(client);
    await waitFor(() => expect(screen.getByText("Importing 0 of 0 supported files")).toBeInTheDocument(), { timeout: 6_000 });

    fireEvent.click(screen.getByRole("link", { name: "Setup" }));
    expect(await screen.findByText("Runtime ready in this app session.")).toBeInTheDocument();

    const statusCallsBeforeFinish = getImportStatus.mock.calls.length;
    await waitFor(
      () => expect(getImportStatus.mock.calls.length).toBeGreaterThanOrEqual(statusCallsBeforeFinish + 2),
      { timeout: 4_000 },
    );

    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "completed",
      result: importResultSummary({ new_assets: 2, duplicate_assets: 1 }),
    });
    await waitFor(() => expect(screen.getByText(/Import Batch result/i)).toBeInTheDocument(), { timeout: 6_000 });
    await waitFor(
      () => expect(getImportStatus.mock.calls.length).toBeGreaterThanOrEqual(statusCallsBeforeFinish + 4),
      { timeout: 8_000 },
    );

    expect(invalidateCalls(invalidateSpy, "assets")).toBe(1);
    expect(invalidateCalls(invalidateSpy, "app-state")).toBe(1);
    expect(invalidateCalls(invalidateSpy, "asset-detail")).toBe(1);
  }, 20_000);

  it("refetches the Library Asset list once when navigating back after a terminal transition", async () => {
    currentImportStatus = importSnapshot();
    const client = createClient();
    renderApp(client);
    await screen.findByRole("button", { name: /first\.gif/i });
    const assetsMock = client.getAssets as ReturnType<typeof vi.fn>;
    expect(assetsMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("link", { name: "Setup" }));
    expect(await screen.findByText("Runtime ready in this app session.")).toBeInTheDocument();

    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
    });
    await waitFor(() => expect(screen.getByText(/Importing 0 of 0 supported files/)).toBeInTheDocument(), { timeout: 6_000 });
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "completed_with_errors",
      failed_files: 1,
      succeeded_files: 2,
      processed_files: 3,
      result: importResultSummary({ processed_files: 3, succeeded_files: 2, failed_files: 1, new_assets: 2 }),
    });
    await waitFor(() => expect(screen.getByText(/Import Batch finished with errors/i)).toBeInTheDocument(), { timeout: 6_000 });
    expect(assetsMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("link", { name: "Library" }));
    await screen.findByRole("button", { name: /first\.gif/i });
    expect(assetsMock).toHaveBeenCalledTimes(2);
  }, 20_000);

  it("refreshes an open Asset detail so newly recorded Source Records appear after duplicate imports", async () => {
    currentImportStatus = importSnapshot();
    const client = createClient();
    renderApp(client);
    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));
    const detailMock = client.getAssetDetail as ReturnType<typeof vi.fn>;
    await screen.findByRole("dialog", { name: "Asset details" });
    expect(detailMock).toHaveBeenCalledTimes(1);

    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
    });
    await waitFor(() => expect(screen.getByText(/Importing 0 of 0 supported files/)).toBeInTheDocument(), { timeout: 6_000 });
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "completed",
      result: importResultSummary({ new_assets: 0, duplicate_assets: 1, source_records_added: 1 }),
    });
    await waitFor(
      () => expect(screen.getByText(/Import Batch completed: 0 new Asset\(s\) and 1 duplicate Asset were added/i))
        .toBeInTheDocument(),
      { timeout: 6_000 },
    );

    await waitFor(() => expect(detailMock).toHaveBeenCalledTimes(2), { timeout: 8_000 });
  }, 25_000);
});

describe("Import Batch progress announcements", () => {
  it("shows discovered and supported scanning progress without claiming a final total", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "scanning",
      running: true,
      selected_sources: 3,
      effective_sources: 3,
      discovered_files: 120,
      supported_files: 45,
    });
    renderApp(createClient());

    const region = await screen.findByRole("status", { name: "Import Batch progress" });
    expect(region.textContent).toContain("Scanning 3 selected sources");
    expect(region.textContent).toContain("120 files discovered");
    expect(region.textContent).toContain("45 supported so far");
    expect(region.textContent).not.toMatch(/of \d+ file/);
  });

  it("shows processed versus supported files and the current source basename while importing", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
      supported_files: 12,
      processed_files: 4,
      current_source_name: "cat.gif",
    });
    renderApp(createClient());

    const region = await screen.findByRole("status", { name: "Import Batch progress" });
    expect(region.textContent).toContain("Importing 4 of 12 supported files");
    expect(region.textContent).toContain("current file: cat.gif");
  });

  it("does not re-announce unchanged progress text on repeated polls", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
      supported_files: 12,
      processed_files: 4,
      current_source_name: "cat.gif",
    });
    renderApp(createClient());
    const region = await screen.findByRole("status", { name: "Import Batch progress" });
    const initialTextNode = region.firstChild;
    expect(initialTextNode).not.toBeNull();

    await waitFor(() => expect(getImportStatus.mock.calls.length).toBeGreaterThanOrEqual(4), {
      timeout: 8_000,
    });
    expect(progressRegion()).toBe(region);
    expect(region.firstChild).toBe(initialTextNode);
    expect(region.textContent).toContain("Importing 4 of 12 supported files");

    currentImportStatus = { ...currentImportStatus, processed_files: 5 };
    await waitFor(() => expect(region.textContent).toContain("Importing 5 of 12 supported files"), {
      timeout: 8_000,
    });
  }, 25_000);
});

describe("Import Batch controls", () => {
  it("explains that the current file will finish when pausing and disables both controls", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "pausing",
      running: true,
      pause_requested: true,
    });
    renderApp(createClient());

    expect(await screen.findByText("Pausing the Import Batch after the current file finishes safely."))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause Import Batch" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resume Import Batch" })).toBeDisabled();
  });

  it("offers Resume while paused and requests it through the typed command", async () => {
    const client = createClient();
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "paused",
      running: true,
      paused: true,
      pause_requested: true,
    });
    renderApp(client);

    expect(await screen.findByRole("button", { name: "Pause Import Batch" })).toBeDisabled();
    const resume = screen.getByRole("button", { name: "Resume Import Batch" });
    expect(resume).toBeEnabled();
    fireEvent.click(resume);
    await waitFor(() => expect(client.resumeImport).toHaveBeenCalledTimes(1));
  });

  it("requests a pause through the typed command while importing", async () => {
    const client = createClient();
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "importing",
      running: true,
    });
    renderApp(client);

    const pause = await screen.findByRole("button", { name: "Pause Import Batch" });
    expect(pause).toBeEnabled();
    fireEvent.click(pause);
    await waitFor(() => expect(client.pauseImport).toHaveBeenCalledTimes(1));
  });

  it("disables new import controls while a batch is active or paused", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "paused",
      running: true,
      paused: true,
      pause_requested: true,
    });
    renderApp(createClient());

    expect(await screen.findByRole("button", { name: "Choose files" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Choose folder" })).toBeDisabled();
  });

  it("enables new import controls again once no batch is running", async () => {
    currentImportStatus = importSnapshot({
      batch_id: "batch-1",
      status: "completed",
      result: importResultSummary({ new_assets: 1 }),
    });
    renderApp(createClient());

    expect(await screen.findByRole("button", { name: "Choose files" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Choose folder" })).toBeEnabled();
  });
});

describe("terminal Import Batch results", () => {
  it("summarizes completed, completed-with-errors, failed, and cancelled outcomes without cancel or failed-only retry actions", async () => {
    const cases: Array<{ snapshot: ImportTask; expected: string[] }> = [
      {
        snapshot: importSnapshot({
          batch_id: "b1",
          status: "completed",
          result: importResultSummary({
            new_assets: 3,
            duplicate_assets: 2,
            unsupported_files: 4,
            jobs_created: 0,
          }),
        }),
        expected: ["3 new Asset(s) and 2 duplicate Asset(s)", "4 file(s) were skipped"],
      },
      {
        snapshot: importSnapshot({
          batch_id: "b2",
          status: "completed_with_errors",
          result: importResultSummary({ processed_files: 9, succeeded_files: 7, failed_files: 2, new_assets: 7 }),
        }),
        expected: ["7 of 9 file(s) imported", "2 failed"],
      },
      {
        snapshot: importSnapshot({
          batch_id: "b3",
          status: "failed",
          partial_result: importResultSummary({ effective_sources: 1, new_assets: 1, duplicate_assets: 0 }),
        }),
        expected: ["Committed before stopping", ""],
      },
      {
        snapshot: importSnapshot({
          batch_id: "b6",
          status: "failed",
          partial_result: importResultSummary({ effective_sources: 1, new_assets: 0, duplicate_assets: 0 }),
        }),
        expected: ["failed before any file was committed", ""],
      },
      {
        snapshot: importSnapshot({
          batch_id: "b4",
          status: "cancelled",
          partial_result: importResultSummary({ effective_sources: 1, new_assets: 4 }),
        }),
        expected: ["Committed Assets remain in the Library", ""],
      },
      {
        snapshot: importSnapshot({ batch_id: "b5", status: "failed" }),
        expected: ["failed before any file was committed", ""],
      },
    ];

    for (const testCase of cases) {
      currentImportStatus = testCase.snapshot;
      const { unmount } = renderApp(createClient());

      const region = await screen.findByRole("status", { name: "Import Batch progress" });
      for (const expected of testCase.expected) {
        if (expected) expect(region.textContent).toContain(expected);
      }
      expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Pause Import Batch" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Resume Import Batch" })).not.toBeInTheDocument();
      unmount();
    }
  });
});
