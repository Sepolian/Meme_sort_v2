import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import type { AssetDetail, AssetListResult } from "./api/types";
import { importSnapshot } from "./features/import/import-test-fixtures";

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
      status: "pending",
    },
    {
      asset_id: "123e4567-e89b-12d3-a456-426614174001",
      library_path: "originals/failed.png",
      library_url: "/media/originals/failed.png",
      thumbnail_url: null,
      media_type: "image/png",
      content_hash: "hash-2",
      width: 120,
      height: 120,
      imported_at: "2026-08-09T00:00:00Z",
      updated_at: "2026-08-09T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/failed.png" }],
      status: "failed",
    },
    {
      asset_id: "123e4567-e89b-12d3-a456-426614174002",
      library_path: "originals/indexed.png",
      library_url: "/media/originals/indexed.png",
      thumbnail_url: "/media/thumbnails/indexed.jpg",
      media_type: "image/png",
      content_hash: "hash-3",
      width: 160,
      height: 90,
      imported_at: "2026-08-09T00:00:00Z",
      updated_at: "2026-08-09T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/indexed.png" }],
      status: "indexed",
    },
  ],
};

const assetDetail: AssetDetail = {
  ...assets.assets[0],
  ocr_status: "ready",
  source_records: [{ source_path: "C:/Source/first.gif", imported_at: "2026-08-09T00:00:00Z", last_seen_at: null }],
  indexed_recipe_labels: [],
  stale_recipe_labels: [],
  ocr_results: [{ result_id: "ocr-1", text: "reaction text", confidence: 0.9, language_hint: "en", created_at: "2026-08-09T00:00:00Z" }],
  renditions: [],
  jobs: [{ job_id: "job-1", type: "embed_asset", status: "pending", recipe_id: "recipe-1", attempt_count: 0 }],
};

const client = {
  getAppState: async () => ({
    library_root: "C:/Library",
    runtime: { backend_name: "llama.cpp", device: "Vulkan0", model_label: "Qwen3-VL", output_dimension: 2048, storage_dtype: "float32" },
    setup_state: {
      health_check_ok: false,
      checklist: [{ id: "health-check", label: "Run runtime health check", done: false, detail: "Vulkan health has not been checked." }],
    },
    library_status: {
      total_assets: 3,
      job_counts: { pending: 2 },
      recent_jobs: [{
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
      }],
    },
  worker_loop: {
    paused: true,
    running: true,
    event_log_path: "C:/Library/logs/worker-loop.jsonl",
    recent_events: [{ event: "worker-loop-paused", payload: {}, timestamp: 1_754_704_800 }],
    persisted_events: [{ event: "tick-finished", payload: { processed_jobs: 1 }, timestamp: 1_754_704_700 }],
  },
  import_task: importSnapshot(),
  pending_jobs: [{ job_id: "job-1" }],
  }),
  getImportStatus: vi.fn(async () => importSnapshot()),
  getAssets: async () => assets,
  getAssetDetail: async () => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    asset: assetDetail,
  }),
  revealAsset: vi.fn(async () => undefined),
  openLogDirectory: vi.fn(async () => undefined),
  deleteAsset: async (assetId: string) => ({
    library_root: "C:/Library",
    asset_id: assetId,
    removed_source_path: null,
    asset_deleted: true,
    removed_source_records: 1,
    removed_jobs: 1,
    removed_renditions: 1,
    removed_embeddings: 1,
  }),
  removeSourceRecord: async (assetId: string, sourcePath: string) => ({
    library_root: "C:/Library",
    asset_id: assetId,
    removed_source_path: sourcePath,
    asset_deleted: false,
    removed_source_records: 1,
    removed_jobs: 0,
    removed_renditions: 0,
    removed_embeddings: 0,
  }),
  batchAssetAction: vi.fn(async (action: "delete" | "rebuild-active-index", assetIds: string[]) => ({
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
  })),
  chooseImportFolder: vi.fn(async () => ({ selected_path: "C:/Source/Memes" })),
  chooseSearchImage: vi.fn(async () => ({ selected_path: "C:/Source/query.png" })),
  chooseLibraryFiles: vi.fn(async () => ({ selection_id: "123e4567-e89b-12d3-a456-426614174010", count: 2 })),
  chooseLibraryFolder: vi.fn(async () => ({ selection_id: "123e4567-e89b-12d3-a456-426614174011", count: 1 })),
  startLibraryImport: vi.fn(async () => importSnapshot({ batch_id: "123e4567-e89b-12d3-a456-426614174020", status: "scanning", running: true, started_at: 1 })),
  startImport: vi.fn(async () => importSnapshot({ batch_id: "123e4567-e89b-12d3-a456-426614174021", status: "scanning", running: true, source_folder: "C:/Source/Memes", started_at: 1 })),
  startImportAndIndex: vi.fn(async () => importSnapshot({ batch_id: "123e4567-e89b-12d3-a456-426614174022", status: "scanning", running: true, source_folder: "C:/Source/Memes", started_at: 1 })),
  pauseImport: vi.fn(async () => importSnapshot({ batch_id: "123e4567-e89b-12d3-a456-426614174021", status: "pausing", running: true, pause_requested: true, source_folder: "C:/Source/Memes", started_at: 1 })),
  resumeImport: vi.fn(async () => importSnapshot({ batch_id: "123e4567-e89b-12d3-a456-426614174021", status: "importing", running: true, source_folder: "C:/Source/Memes", started_at: 1 })),
  searchText: vi.fn(async (query: string) => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    query,
    top_k: 18,
    results: [{ asset_id: "123e4567-e89b-12d3-a456-426614174000", library_url: "/media/originals/first.gif", thumbnail_url: "/media/thumbnails/first.jpg", library_path: "originals/first.gif", media_type: "image/gif", score: 0.92, match_sources: ["visual"] }],
  })),
  searchImage: vi.fn(async () => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    query_path: "C:/Source/query.png",
    query_media_type: "image/png",
    top_k: 18,
    results: [{ asset_id: "123e4567-e89b-12d3-a456-426614174000", library_url: "/media/originals/first.gif", thumbnail_url: "/media/thumbnails/first.jpg", library_path: "originals/first.gif", media_type: "image/gif", score: 0.92, match_sources: ["visual"] }],
  })),
  findSimilar: vi.fn(async (assetId: string) => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    asset_id: assetId,
    top_k: 18,
    results: [{ asset_id: "123e4567-e89b-12d3-a456-426614174000", library_url: "/media/originals/first.gif", thumbnail_url: "/media/thumbnails/first.jpg", library_path: "originals/first.gif", media_type: "image/gif", score: 0.92, match_sources: ["visual"] }],
  })),
  getDuplicates: vi.fn(async (threshold: number) => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    threshold,
    pairs: [{
      score: 0.97,
      asset_a_id: "123e4567-e89b-12d3-a456-426614174000",
      asset_b_id: "123e4567-e89b-12d3-a456-426614174002",
      asset_a_path: "originals/first.gif",
      asset_b_path: "originals/indexed.png",
      asset_a_thumbnail_url: "/media/thumbnails/first.jpg",
      asset_b_thumbnail_url: "/media/thumbnails/indexed.jpg",
      asset_a_matched_source_ref: "frame:2",
      asset_b_matched_source_ref: null,
    }],
  })),
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
    diagnostic_steps: [{ step: "image-embedding-smoke", status: "ok", detail: "Image embedding passed." }],
    smoke_test_ok: true,
    error: null,
  })),
  retryFailedJobs: vi.fn(async () => ({
    library_root: "C:/Library",
    retried_jobs: 2,
    failed_jobs_remaining: 0,
  })),
  getPendingJobs: vi.fn(async () => ({
    jobs: [{
      job_id: "123e4567-e89b-12d3-a456-426614174003",
      type: "embed_asset",
      asset_id: "123e4567-e89b-12d3-a456-426614174002",
      asset_path: "originals/indexed.png",
      recipe_id: "recipe-1",
      attempt_count: 0,
      created_at: "2026-08-09T00:00:00Z",
      updated_at: "2026-08-09T00:00:00Z",
    }],
  })),
  deletePendingJobs: vi.fn(async (jobIds: string[]) => ({ requested_job_ids: jobIds, deleted_job_ids: jobIds, skipped_job_ids: [] })),
  cancelSearch: vi.fn(async (requestId: string) => ({ request_id: requestId, cancelled: true, was_active: true })),
};

describe("App", () => {
  function renderApp(route = "/") {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <App client={client} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("shows state returned through the typed Tauri client", async () => {
    renderApp();

    expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();
    expect(screen.getByText("llama.cpp / Vulkan0")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(await screen.findByText("Pending Asset")).toBeInTheDocument();
    expect(screen.getByText("Failed Asset")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "first.gif preview" })).toHaveAttribute(
      "src",
      "http://memesort-media.localhost/media/thumbnails/first.jpg",
    );
  });

  it("opens Asset detail for a browseable Pending Asset", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));

    expect(await screen.findByRole("dialog", { name: "Asset details" })).toBeInTheDocument();
    expect(await screen.findByText("reaction text")).toBeInTheDocument();
    expect(screen.getByText("C:/Source/first.gif")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    expect(screen.getByRole("alertdialog", { name: "Delete this Asset?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  });

  it("reveals an Asset's managed Library Copy and its recorded Source Path", async () => {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));

    fireEvent.click(await screen.findByRole("button", { name: "Reveal Managed File" }));
    expect(await screen.findByText("Opened the managed Library Copy in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "managed");

    fireEvent.click(screen.getByRole("button", { name: "Reveal Source" }));
    expect(await screen.findByText("Opened the recorded Source Path in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174000", "source", "C:/Source/first.gif");
  });

  it("confirms and queues a selected Active Index rebuild", async () => {
    renderApp();
    fireEvent.click(await screen.findByLabelText("Select first.gif"));
    fireEvent.click(screen.getByRole("button", { name: "Rebuild Active Index" }));

    expect(screen.getByRole("alertdialog", { name: "Rebuild 1 selected Asset(s)?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Queue rebuild" }));

    expect(await screen.findByText("Queued 1 Active Index rebuild(s); skipped 0 running Asset(s)."))
      .toBeInTheDocument();
    expect(client.batchAssetAction).toHaveBeenCalledWith("rebuild-active-index", ["123e4567-e89b-12d3-a456-426614174000"]);
  });

  it.each([
    ["/", "Your library"],
    ["/setup", "Setup & runtime"],
    ["/search", "Search MemeSort"],
    ["/search/text", "Text search"],
    ["/search/image", "Image search"],
    ["/search/similar", "Find similar Assets"],
    ["/duplicates", "Duplicate assets"],
    ["/status", "Application status"],
  ])("renders the %s route when it is opened directly", async (route, heading) => {
    renderApp(route);

    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("shows a runtime-not-ready state on setup", async () => {
    renderApp("/setup");

    expect(await screen.findByText("Runtime not ready")).toBeInTheDocument();
  });

  it("shows the read-only Runtime Descriptor and setup checklist", async () => {
    renderApp("/setup");

    expect(await screen.findByRole("heading", { name: "Runtime Descriptor" })).toBeInTheDocument();
    expect(screen.getByText("Qwen3-VL · 2048d · float32")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Setup checklist" })).toBeInTheDocument();
    expect(screen.getByText("Next · Run runtime health check · Vulkan health has not been checked.")).toBeInTheDocument();
  });

  it("runs the pinned Vulkan health check and displays its diagnostic step", async () => {
    renderApp("/setup");

    fireEvent.click(await screen.findByRole("button", { name: "Run Vulkan health check" }));

    expect(client.runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Runtime health check passed on Vulkan0.")).toBeInTheDocument();
    expect(screen.getByText("image-embedding-smoke · ok · Image embedding passed.")).toBeInTheDocument();
  });

  it("submits a UUID-scoped text Search Request and shows its Asset matches", async () => {
    renderApp("/search/text");

    fireEvent.change(await screen.findByLabelText("Search text"), { target: { value: "surprised reaction" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("originals/first.gif")).toBeInTheDocument();
    expect(client.searchText).toHaveBeenCalledWith("surprised reaction", expect.any(String));
  });

  it("searches an image selected through the native dialog", async () => {
    renderApp("/search/image");

    expect(await screen.findByRole("button", { name: "Search image" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Choose image" }));
    expect(await screen.findByText("C:/Source/query.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search image" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Search image" }));

    expect(await screen.findByRole("region", { name: "Image search results" })).toBeInTheDocument();
    expect(client.searchImage).toHaveBeenCalledWith(expect.any(String));
    expect(screen.getByText("originals/first.gif")).toBeInTheDocument();
  });

  it("finds Assets similar to a selected Indexed Asset", async () => {
    renderApp("/search/similar");

    const selector = await screen.findByLabelText("Indexed Asset");
    await screen.findByRole("option", { name: "originals/indexed.png" });
    expect(screen.getByRole("button", { name: "Find similar" })).toBeDisabled();
    fireEvent.change(selector, { target: { value: "123e4567-e89b-12d3-a456-426614174002" } });
    expect(screen.getByRole("button", { name: "Find similar" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Find similar" }));

    expect(await screen.findByRole("region", { name: "Similar Asset results" })).toBeInTheDocument();
    expect(client.findSimilar).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174002");
    expect(screen.getByText("originals/first.gif")).toBeInTheDocument();
  });

  it("reviews duplicate Asset pairs at the chosen threshold", async () => {
    renderApp("/duplicates");

    fireEvent.change(await screen.findByLabelText("Duplicate threshold"), { target: { value: "0.95" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan duplicates" }));

    expect(await screen.findByRole("region", { name: "Duplicate pairs" })).toBeInTheDocument();
    expect(client.getDuplicates).toHaveBeenCalledWith(0.95);
    expect(screen.getByText("originals/first.gif")).toBeInTheDocument();
    expect(screen.getByText("originals/indexed.png")).toBeInTheDocument();
    expect(screen.getByText(/GIF matches use the strongest frame-to-frame score/i)).toBeInTheDocument();
  });

  it("resumes the paused Worker Loop through the typed desktop command", async () => {
    renderApp("/status");

    fireEvent.click(await screen.findByRole("button", { name: "Resume worker" }));

    expect(client.resumeWorkerLoop).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Worker Loop resumed.")).toBeInTheDocument();
  });

  it("retries failed Job records without changing Assets", async () => {
    renderApp("/status");

    fireEvent.click(await screen.findByRole("button", { name: "Retry failed Jobs" }));

    expect(client.retryFailedJobs).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Retried 2 failed Job record(s); 0 remain failed.")).toBeInTheDocument();
  });

  it("opens the Library log directory through the native desktop command", async () => {
    renderApp("/status");

    fireEvent.click(await screen.findByRole("button", { name: "Open log folder" }));

    expect(client.openLogDirectory).toHaveBeenCalledTimes(1);
  });

  it("shows Recent Jobs and worker events from the read-only app-state projection", async () => {
    renderApp("/status");

    expect(await screen.findByRole("heading", { name: "Recent Jobs" })).toBeInTheDocument();
    expect(screen.getByText("embed_asset · failed · attempt 2")).toBeInTheDocument();
    expect(screen.getByText("The embedding worker stopped.")).toBeInTheDocument();
    expect(screen.getByText("123e4567-e89b-12d3-a456-426614174002 · updated 2026-08-09T01:00:00Z")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Worker events" })).toBeInTheDocument();
    expect(screen.getByText("worker-loop-paused")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Persisted worker log" })).toBeInTheDocument();
    expect(screen.getByText("tick-finished")).toBeInTheDocument();
    expect(screen.getByText('{"processed_jobs":1}')).toBeInTheDocument();
  });

  it("confirms deletion of selected Pending Job records without deleting Assets", async () => {
    renderApp("/status");

    fireEvent.click(await screen.findByLabelText("Select Pending Job embed_asset"));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected Pending Jobs" }));
    expect(screen.getByRole("alertdialog", { name: "Delete 1 Pending Job(s)?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete Pending Jobs" }));

    expect(client.deletePendingJobs).toHaveBeenCalledWith(["123e4567-e89b-12d3-a456-426614174003"]);
    expect(await screen.findByText("Deleted 1 Pending Job record(s); skipped 0.")).toBeInTheDocument();
  });

  it("imports only a folder selected through the native dialog", async () => {
    renderApp("/setup");

    fireEvent.click(await screen.findByRole("button", { name: "Choose import folder" }));
    expect(await screen.findByText("C:/Source/Memes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import folder" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Import folder" }));

    expect(client.chooseImportFolder).toHaveBeenCalledTimes(1);
    expect(client.startImport).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Import started in the background.")).toBeInTheDocument();
  });

  it("chooses Library files and starts the Import Batch from the selection ID", async () => {
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Choose files" }));

    expect(await screen.findByText(/Import Batch started for 2 files/)).toBeInTheDocument();
    expect(client.chooseLibraryFiles).toHaveBeenCalledTimes(1);
    expect(client.startLibraryImport).toHaveBeenCalledWith(
      "123e4567-e89b-12d3-a456-426614174010",
    );
    expect(screen.getByRole("status", { name: "Import Batch progress" })).toHaveTextContent(
      "Scanning 0 selected sources · 0 files discovered · 0 supported so far.",
    );
  });

  it("chooses a Library folder and starts the Import Batch from the selection ID", async () => {
    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "Choose folder" }));

    expect(await screen.findByText(/Import Batch started for 1 folder/)).toBeInTheDocument();
    expect(client.chooseLibraryFolder).toHaveBeenCalledTimes(1);
    expect(client.startLibraryImport).toHaveBeenCalledWith(
      "123e4567-e89b-12d3-a456-426614174011",
    );
  });

  it("closes the keyboard help dialog with Escape", async () => {
    renderApp("/status");
    await screen.findByRole("heading", { name: "Application status" });

    fireEvent.click(screen.getByRole("button", { name: "Keyboard help" }));
    expect(screen.getByRole("dialog", { name: "MemeSort navigation" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "MemeSort navigation" })).not.toBeInTheDocument();
  });
});
