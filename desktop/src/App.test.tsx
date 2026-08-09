import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import type { AssetDetail, AssetListResult } from "./api/types";

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
    runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
    setup_state: { health_check_ok: false },
    library_status: { total_assets: 3, job_counts: { pending: 2 } },
  worker_loop: { paused: true, running: true },
  import_task: {
    status: "idle",
    running: false,
    paused: false,
    pause_requested: false,
    source_folder: null,
    started_at: null,
    finished_at: null,
    result: null,
    error: null,
  },
  pending_jobs: [{ job_id: "job-1" }],
  }),
  getAssets: async () => assets,
  getAssetDetail: async () => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    asset: assetDetail,
  }),
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
  startImport: vi.fn(async () => ({ status: "running", running: true, paused: false, pause_requested: false, source_folder: "C:/Source/Memes", started_at: 1, finished_at: null, result: null, error: null })),
  startImportAndIndex: vi.fn(async () => ({ status: "running", running: true, paused: false, pause_requested: false, source_folder: "C:/Source/Memes", started_at: 1, finished_at: null, result: null, error: null })),
  pauseImport: vi.fn(async () => ({ status: "pausing", running: true, paused: false, pause_requested: true, source_folder: "C:/Source/Memes", started_at: 1, finished_at: null, result: null, error: null })),
  resumeImport: vi.fn(async () => ({ status: "running", running: true, paused: false, pause_requested: false, source_folder: "C:/Source/Memes", started_at: 1, finished_at: null, result: null, error: null })),
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

  it("closes the keyboard help dialog with Escape", async () => {
    renderApp("/status");
    await screen.findByRole("heading", { name: "Application status" });

    fireEvent.click(screen.getByRole("button", { name: "Keyboard help" }));
    expect(screen.getByRole("dialog", { name: "MemeSort navigation" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "MemeSort navigation" })).not.toBeInTheDocument();
  });
});
