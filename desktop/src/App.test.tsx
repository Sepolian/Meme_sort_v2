import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
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
    pending_jobs: [{ job_id: "job-1" }],
  }),
  getAssets: async () => assets,
  getAssetDetail: async () => ({
    library_root: "C:/Library",
    active_recipe_id: "recipe-1",
    active_recipe_label: "Vulkan0 recipe",
    asset: assetDetail,
  }),
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

  it("closes the keyboard help dialog with Escape", async () => {
    renderApp("/status");
    await screen.findByRole("heading", { name: "Application status" });

    fireEvent.click(screen.getByRole("button", { name: "Keyboard help" }));
    expect(screen.getByRole("dialog", { name: "MemeSort navigation" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "MemeSort navigation" })).not.toBeInTheDocument();
  });
});
