import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import type { AssetDetail, AssetListResult, SearchAsset } from "./api/types";
import { importSnapshot } from "./features/import/import-test-fixtures";
import { resetRuntimeHealthForTesting } from "./features/runtime/runtimeHealthStore";

const CAT_ID = "123e4567-e89b-12d3-a456-426614174001";
const DOG_ID = "123e4567-e89b-12d3-a456-426614174002";
const BIRD_ID = "123e4567-e89b-12d3-a456-426614174003";
const ZEBRA_ID = "123e4567-e89b-12d3-a456-426614174004";
const MISSING_ID = "123e4567-e89b-12d3-a456-426614174099";

const assets: AssetListResult = {
  library_root: "C:/Library",
  active_recipe_id: "recipe-1",
  active_recipe_label: "Vulkan0 recipe",
  assets: [
    {
      asset_id: CAT_ID,
      library_path: "originals/cat-meme.png",
      library_url: "/media/originals/cat-meme.png",
      thumbnail_url: "/media/thumbnails/cat-meme.jpg",
      media_type: "image/png",
      content_hash: "hash-cat",
      width: 200,
      height: 100,
      imported_at: "2026-08-10T00:00:00Z",
      updated_at: "2026-08-10T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/cat-meme.png" }],
      status: "indexed",
    },
    {
      asset_id: ZEBRA_ID,
      library_path: "originals/zebra.png",
      library_url: "/media/originals/zebra.png",
      thumbnail_url: "/media/thumbnails/zebra.jpg",
      media_type: "image/png",
      content_hash: "hash-zebra",
      width: 160,
      height: 90,
      imported_at: "2026-08-09T00:00:00Z",
      updated_at: "2026-08-09T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/zebra.png" }],
      status: "indexed",
    },
    {
      asset_id: DOG_ID,
      library_path: "originals/dog-park.png",
      library_url: "/media/originals/dog-park.png",
      thumbnail_url: "/media/thumbnails/dog-park.jpg",
      media_type: "image/png",
      content_hash: "hash-dog",
      width: 120,
      height: 120,
      imported_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/dog-park.png" }],
      status: "pending",
    },
    {
      asset_id: BIRD_ID,
      library_path: "originals/bird.png",
      library_url: "/media/originals/bird.png",
      thumbnail_url: "/media/thumbnails/bird.jpg",
      media_type: "image/png",
      content_hash: "hash-bird",
      width: 140,
      height: 100,
      imported_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      source_record_count: 1,
      source_records: [{ source_path: "C:/Source/bird-photo.png" }],
      status: "failed",
    },
  ],
};

function detailFor(assetId: string): AssetDetail {
  const base = assets.assets.find((a) => a.asset_id === assetId)!;
  return {
    ...base,
    ocr_status: "ready",
    source_records: base.source_records.map((s) => ({
      source_path: s.source_path,
      imported_at: base.imported_at,
      last_seen_at: null,
    })),
    indexed_recipe_labels: [],
    stale_recipe_labels: [],
    ocr_results: [],
    renditions: [],
    jobs: [],
  };
}

function searchAsset(overrides: Partial<SearchAsset> & { asset_id: string }): SearchAsset {
  return {
    library_url: `/media/originals/${overrides.asset_id.slice(-4)}.png`,
    thumbnail_url: null,
    library_path: `originals/${overrides.asset_id.slice(-4)}.png`,
    media_type: "image/png",
    score: 0.5,
    match_sources: ["visual"],
    ocr_snippet: null,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    getAppState: async () => ({
      library_root: "C:/Library",
      runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
      setup_state: { health_check_ok: true },
      library_status: { total_assets: 4, job_counts: { pending: 1 } },
      worker_loop: { paused: false, running: true },
      import_task: importSnapshot(),
      pending_jobs: [],
    }),
    getImportStatus: async () => importSnapshot(),
    getAssets: async () => assets,
    getAssetDetail: vi.fn(async (assetId: string) => ({
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      asset: detailFor(assetId),
    })),
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
    chooseSearchImage: vi.fn(async () => ({ selected_path: "C:/Source/query.png" })),
    chooseLibraryFiles: async () => {
      throw new Error("not under test");
    },
    chooseLibraryFolder: async () => {
      throw new Error("not under test");
    },
    startLibraryImport: async () => {
      throw new Error("not under test");
    },
    pauseImport: async () => {
      throw new Error("not under test");
    },
    resumeImport: async () => {
      throw new Error("not under test");
    },
    searchText: vi.fn(async () => {
      throw new Error("searchText mock not configured");
    }),
    searchImage: vi.fn(async () => {
      throw new Error("searchImage mock not configured");
    }),
    findSimilar: vi.fn(async () => {
      throw new Error("findSimilar mock not configured");
    }),
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
    cancelSearch: vi.fn(async (requestId: string) => ({
      request_id: requestId,
      cancelled: true,
      was_active: true,
    })),
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
  };
}

function renderApp(route: string | string[], client: ReturnType<typeof makeClient>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={Array.isArray(route) ? route : [route]}>
        <App client={client as never} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, queryClient };
}

function cardOrder(): string[] {
  const buttons = screen.getAllByRole("button", { name: /^Open / });
  return buttons.map((b) => b.getAttribute("aria-label") ?? "");
}

async function typeQuery(value: string) {
  const input = await screen.findByLabelText("Search Library");
  fireEvent.change(input, { target: { value } });
  return input;
}

async function submitTextSearch() {
  const searchForm = screen.getByRole("search", { name: "Library search" });
  fireEvent.click(searchForm.querySelector('button[type="submit"]')!);
}

async function clickImageSearch() {
  fireEvent.click(screen.getByRole("button", { name: "Search by image" }));
}

function flush() {
  return act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("Image search and Find Similar (ticket 12)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRuntimeHealthForTesting();
    vi.clearAllMocks();
  });

  it("cancelling the native picker leaves Library state unchanged", async () => {
    const client = makeClient({
      chooseSearchImage: vi.fn(async () => ({ selected_path: null })),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });
    const before = cardOrder();

    await clickImageSearch();
    await flush();

    expect(client.chooseSearchImage).toHaveBeenCalledTimes(1);
    expect(client.searchImage).not.toHaveBeenCalled();
    expect(client.cancelSearch).not.toHaveBeenCalled();
    expect(cardOrder()).toEqual(before);
    expect(screen.queryByRole("status", { name: "Image search results" })).not.toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Similar search results" })).not.toBeInTheDocument();
  });

  it("picker cancel after semantic results keeps the semantic results", async () => {
    const client = makeClient({
      chooseSearchImage: vi.fn(async () => ({ selected_path: null })),
      searchText: vi.fn(async () => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "meme",
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.9 })],
      })),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("meme");
    await submitTextSearch();
    expect(await screen.findByText(/Semantic results for/)).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);

    await clickImageSearch();
    await flush();

    expect(client.searchImage).not.toHaveBeenCalled();
    expect(await screen.findByText(/Semantic results for/)).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("a selected query image starts a fresh UUID-scoped Search Request", async () => {
    const gate = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchImage: vi.fn(async () => gate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    await screen.findByRole("status", { name: "Image search results" });

    expect(client.chooseSearchImage).toHaveBeenCalledTimes(1);
    expect(client.searchImage).toHaveBeenCalledTimes(1);
    const requestId = (client.searchImage as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(typeof requestId).toBe("string");
    expect(requestId.length).toBeGreaterThan(8);
    expect(requestId).toMatch(/^[0-9a-f-]{8,}$/i);
    expect(await screen.findByText(/Searching the Active Index Recipe for the chosen image/)).toBeInTheDocument();

    await act(async () => {
      gate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.88 })],
      } as never);
    });
    expect(await screen.findByText(/Image results ·/)).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);
  });

  it("identifies the selected image and retries the same visual query", async () => {
    const client = makeClient({
      searchImage: vi
        .fn()
        .mockRejectedValueOnce({ error: "SidecarError", detail: "image index unavailable", retryable: true })
        .mockResolvedValueOnce({
          library_root: "C:/Library",
          active_recipe_id: "recipe-1",
          active_recipe_label: "Vulkan0 recipe",
          query_path: "C:/Source/query.png",
          query_media_type: "image/png",
          top_k: 18,
          results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.88 })],
        }),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    const results = await screen.findByRole("status", { name: "Image search results" });
    expect(results).toHaveTextContent("Query image: “query.png”");
    const error = await screen.findByRole("alert", { name: "Image search error" });
    expect(error).toHaveTextContent("Query image: “query.png”");

    const retry = within(error).getByRole("button", { name: "Retry image search" });
    retry.focus();
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByLabelText("Search Library")).toHaveFocus());
    expect(client.searchImage).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Image results · 1")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Image search results" })).toHaveTextContent("Query image: “query.png”");
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("keeps the image query visible and offers re-selection after picker cancellation", async () => {
    const client = makeClient({
      chooseSearchImage: vi.fn()
        .mockResolvedValueOnce({ selected_path: "C:/Source/query.png" })
        .mockResolvedValueOnce({ selected_path: null })
        .mockResolvedValueOnce({ selected_path: "C:/Source/replacement.png" }),
      searchImage: vi
        .fn()
        .mockRejectedValueOnce({ error: "SidecarError", detail: "image search failed", retryable: true })
        .mockResolvedValueOnce({
          library_root: "C:/Library",
          active_recipe_id: "recipe-1",
          active_recipe_label: "Vulkan0 recipe",
          query_path: "C:/Source/replacement.png",
          query_media_type: "image/png",
          top_k: 18,
          results: [searchAsset({ asset_id: CAT_ID, score: 0.9 })],
        }),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    const error = await screen.findByRole("alert", { name: "Image search error" });
    expect(error).toHaveTextContent("Query image: “query.png”");

    await clickImageSearch();
    await flush();
    expect(screen.getByRole("alert", { name: "Image search error" })).toHaveTextContent("Query image: “query.png”");
    expect(screen.queryByRole("button", { name: "Retry image search" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose another image" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Choose another image" }));
    expect(await screen.findByText("Image results · 1")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Image search results" })).toHaveTextContent("Query image: “replacement.png”");
    expect(client.searchImage).toHaveBeenCalledTimes(2);
  });

  it("does not offer retry for a non-retryable visual failure", async () => {
    const client = makeClient({
      searchImage: vi.fn(async () => {
        throw { error: "SidecarError", detail: "image file is invalid", retryable: false };
      }),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    const error = await screen.findByRole("alert", { name: "Image search error" });
    expect(error).toHaveTextContent("image file is invalid");
    expect(error).toHaveTextContent("cannot be retried");
    expect(within(error).queryByRole("button", { name: "Retry image search" })).not.toBeInTheDocument();
    expect(within(error).getByRole("button", { name: "Choose another image" })).toBeInTheDocument();
  });

  it("starting image search cancels an older cancellable Search Request", async () => {
    const first = deferred<Record<string, unknown>>();
    const second = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn(async () => first.promise as never),
      searchImage: vi.fn(async () => second.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("first");
    await submitTextSearch();
    const firstId = (client.searchText as ReturnType<typeof vi.fn>).mock.calls[0][1] as string;

    await clickImageSearch();
    expect(client.searchImage).toHaveBeenCalledTimes(1);
    expect(client.cancelSearch).toHaveBeenCalledTimes(1);
    expect(client.cancelSearch).toHaveBeenCalledWith(firstId);

    await act(async () => {
      second.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [],
      } as never);
    });
    await act(async () => {
      first.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "first",
        top_k: 18,
        results: [],
      } as never);
    });
    await flush();
  });

  it("cross-mode latest wins: image started last beats an older text completion", async () => {
    const textGate = deferred<Record<string, unknown>>();
    const imageGate = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn(async () => textGate.promise as never),
      searchImage: vi.fn(async () => imageGate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("meme");
    await submitTextSearch();
    await clickImageSearch();

    await act(async () => {
      imageGate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.9 })],
      } as never);
    });
    expect(await screen.findByRole("button", { name: /zebra\.png/ })).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);

    await act(async () => {
      textGate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "meme",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.99 })],
      } as never);
    });
    await flush();
    // Older text must not overwrite the latest image results.
    expect(cardOrder()).toEqual(["Open zebra.png"]);
    expect(screen.queryByRole("button", { name: /cat-meme\.png/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Image search results" })).toBeInTheDocument();
  });

  it("cross-mode latest wins: text started last beats an older image completion", async () => {
    const textGate = deferred<Record<string, unknown>>();
    const imageGate = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn(async () => textGate.promise as never),
      searchImage: vi.fn(async () => imageGate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    expect(client.searchImage).toHaveBeenCalledTimes(1);
    const imageId = (client.searchImage as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;

    await typeQuery("meme");
    await submitTextSearch();
    // Starting text cancels the waiting image request.
    expect(client.cancelSearch).toHaveBeenCalledWith(imageId);

    await act(async () => {
      textGate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "meme",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.95 })],
      } as never);
    });
    expect(await screen.findByText(/Semantic results for/)).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);

    await act(async () => {
      imageGate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.99 })],
      } as never);
    });
    await flush();
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);
    expect(screen.queryByRole("button", { name: /zebra\.png/ })).not.toBeInTheDocument();
  });

  it("cross-mode latest wins: similar started last beats an older text completion", async () => {
    const textGate = deferred<Record<string, unknown>>();
    const similarGate = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn(async () => textGate.promise as never),
      findSimilar: vi.fn(async () => similarGate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("meme");
    await submitTextSearch();
    const textId = (client.searchText as ReturnType<typeof vi.fn>).mock.calls[0][1] as string;

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    expect(client.findSimilar).toHaveBeenCalledWith(CAT_ID);
    // Starting similar cancels the older cancellable text request.
    expect(client.cancelSearch).toHaveBeenCalledWith(textId);

    await act(async () => {
      similarGate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
      } as never);
    });
    expect(await screen.findByRole("status", { name: "Similar search results" })).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);

    await act(async () => {
      textGate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "meme",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.99 })],
      } as never);
    });
    await flush();
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("Find Similar from the inspector passes the Asset ID and uses the similar mode", async () => {
    const client = makeClient({
      findSimilar: vi.fn(async (assetId: string) => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: assetId,
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
      })),
    });
    renderApp(`/?asset=${CAT_ID}`, client);
    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy to Clipboard" });

    fireEvent.click(screen.getByRole("button", { name: "Find Similar" }));
    expect(client.findSimilar).toHaveBeenCalledTimes(1);
    expect(client.findSimilar).toHaveBeenCalledWith(CAT_ID);

    expect(await screen.findByRole("status", { name: "Similar search results" })).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("Find Similar from a card passes the same Asset ID and produces the same mode", async () => {
    const client = makeClient({
      findSimilar: vi.fn(async (assetId: string) => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: assetId,
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
      })),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    expect(client.findSimilar).toHaveBeenCalledTimes(1);
    expect(client.findSimilar).toHaveBeenCalledWith(CAT_ID);

    expect(await screen.findByRole("status", { name: "Similar search results" })).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("composed image results preserve relevance order, exclude non-Indexed, omit stale, and hide scores", async () => {
    const client = makeClient({
      searchImage: vi.fn(async () => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [
          searchAsset({ asset_id: ZEBRA_ID, score: 0.91, match_sources: ["visual"] }),
          searchAsset({ asset_id: DOG_ID, score: 0.85, match_sources: ["visual"] }),
          searchAsset({ asset_id: CAT_ID, score: 0.82, match_sources: ["visual", "ocr"], ocr_snippet: "funny" }),
          searchAsset({ asset_id: MISSING_ID, score: 0.7, match_sources: ["visual"] }),
        ],
      })),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    expect(await screen.findByRole("status", { name: "Image search results" })).toBeInTheDocument();
    // Relevance order preserved (zebra before cat), pending dog excluded, missing stale.
    expect(cardOrder()).toEqual(["Open zebra.png", "Open cat-meme.png"]);
    expect(screen.queryByRole("button", { name: /dog-park\.png/ })).not.toBeInTheDocument();
    expect(screen.getByText(/1 image result.*omitted/i)).toBeInTheDocument();

    const details = screen.getByText("Advanced details").closest("details")!;
    const scoreInDetails = within(details as HTMLElement).getByText(/score 0\.910/);
    expect(scoreInDetails).toBeInTheDocument();
    const allScores = screen.getAllByText(/score 0\./);
    for (const node of allScores) {
      expect(details.contains(node)).toBe(true);
    }
  });

  it("composed similar results preserve similarity order, omit stale, and hide scores", async () => {
    const client = makeClient({
      findSimilar: vi.fn(async () => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [
          searchAsset({ asset_id: ZEBRA_ID, score: 0.97, match_sources: ["visual"] }),
          searchAsset({ asset_id: BIRD_ID, score: 0.9, match_sources: ["visual"] }),
          searchAsset({ asset_id: CAT_ID, score: 0.88, match_sources: ["visual"] }),
          searchAsset({ asset_id: MISSING_ID, score: 0.6, match_sources: ["visual"] }),
        ],
      })),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    expect(await screen.findByRole("status", { name: "Similar search results" })).toBeInTheDocument();
    // Failed bird excluded, stale missing omitted, similarity order kept.
    expect(cardOrder()).toEqual(["Open zebra.png", "Open cat-meme.png"]);
    expect(screen.getByText(/1 similar result.*omitted/i)).toBeInTheDocument();

    const details = screen.getByText("Advanced details").closest("details")!;
    expect(within(details as HTMLElement).getByText(/score 0\.970/)).toBeInTheDocument();
    for (const node of screen.getAllByText(/score 0\./)) {
      expect(details.contains(node)).toBe(true);
    }
  });

  it("clearing image results restores browsing and cancels the active request", async () => {
    const gate = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchImage: vi.fn(async () => gate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);

    await clickImageSearch();
    const requestId = (client.searchImage as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(await screen.findByRole("status", { name: "Image search results" })).toBeInTheDocument();

    const section = screen.getByRole("status", { name: "Image search results" });
    fireEvent.click(within(section as HTMLElement).getByRole("button", { name: "Clear search" }));
    expect(client.cancelSearch).toHaveBeenCalledWith(requestId);
    expect(await screen.findByRole("button", { name: /bird-photo\.png/ })).toBeInTheDocument();
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);
    expect(screen.queryByRole("status", { name: "Image search results" })).not.toBeInTheDocument();
    expect(client.searchImage).toHaveBeenCalledTimes(1);

    await act(async () => {
      gate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.9 })],
      } as never);
    });
    await flush();
    // Late completion after clear must not invent results.
    expect(screen.queryByRole("status", { name: "Image search results" })).not.toBeInTheDocument();
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);
  });

  it("reloading with image/similar URL params never restores results and nothing is persisted", async () => {
    const client = makeClient();
    renderApp("/?q=cat&image=foo&similar=bar", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    expect(client.searchImage).not.toHaveBeenCalled();
    expect(client.findSimilar).not.toHaveBeenCalled();
    expect(screen.queryByRole("status", { name: "Image search results" })).not.toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Similar search results" })).not.toBeInTheDocument();
    // `q` still drives local filtering only.
    expect(await screen.findByText(/Local matches for/)).toBeInTheDocument();
    expect(localStorage.getItem("memesort.library.mode/v1")).toBeNull();
  });

  it("leaving Library cancels an active image request", async () => {
    const gate = deferred<Record<string, unknown>>();
    const client = makeClient({
      searchImage: vi.fn(async () => gate.promise as never),
    });
    const { unmount } = renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    const requestId = (client.searchImage as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;

    unmount();
    expect(client.cancelSearch).toHaveBeenCalledWith(requestId);
    await act(async () => {
      gate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [],
      } as never);
    });
    await flush();
  });

  it("a failed image picker preserves already committed similar results", async () => {
    const client = makeClient({
      chooseSearchImage: vi.fn(async () => {
        throw new Error("native picker unavailable");
      }),
      findSimilar: vi.fn(async () => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
      })),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 1");
    });
    expect(cardOrder()).toEqual(["Open zebra.png"]);

    await clickImageSearch();
    await flush();

    expect(client.chooseSearchImage).toHaveBeenCalledTimes(1);
    expect(client.searchImage).not.toHaveBeenCalled();
    expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 1");
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("repeating Find Similar for the same Asset retains the prior result while pending and after an error", async () => {
    const repeat = deferred<never>();
    const client = makeClient({
      findSimilar: vi
        .fn()
        .mockResolvedValueOnce({
          library_root: "C:/Library",
          active_recipe_id: "recipe-1",
          active_recipe_label: "Vulkan0 recipe",
          asset_id: CAT_ID,
          top_k: 18,
          results: [searchAsset({ asset_id: CAT_ID, score: 0.93 })],
        })
        .mockReturnValueOnce(repeat.promise),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 1");
    });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    const pending = screen.getByRole("status", { name: "Similar search results" });
    expect(pending).toHaveTextContent("Similar results · 1");
    expect(pending).toHaveTextContent("Finding similar Assets in the Active Index Recipe…");
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);

    await act(async () => {
      repeat.reject({ error: "SidecarError", detail: "similar index unavailable", retryable: true });
    });

    expect(await screen.findByRole("alert", { name: "Similar search error" })).toHaveTextContent("similar index unavailable");
    expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 1");
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);
  });

  it("identifies the source Asset and retries a failed Find Similar request", async () => {
    const client = makeClient({
      findSimilar: vi
        .fn()
        .mockRejectedValueOnce({ error: "SidecarError", detail: "similar index unavailable", retryable: true })
        .mockResolvedValueOnce({
          library_root: "C:/Library",
          active_recipe_id: "recipe-1",
          active_recipe_label: "Vulkan0 recipe",
          asset_id: CAT_ID,
          top_k: 18,
          results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
        }),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    const results = await screen.findByRole("status", { name: "Similar search results" });
    expect(results).toHaveTextContent("Source Asset: “cat-meme.png”");
    const error = await screen.findByRole("alert", { name: "Similar search error" });
    expect(error).toHaveTextContent("Source Asset: “cat-meme.png”");

    fireEvent.click(within(error).getByRole("button", { name: "Retry similar search" }));
    expect(await screen.findByText("Similar results · 1")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Source Asset: “cat-meme.png”");
    expect(client.findSimilar).toHaveBeenCalledTimes(2);
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("a late similar failure and finally cannot clear a newer image request's loading state", async () => {
    const first = deferred<never>();
    const second = deferred<Record<string, unknown>>();
    const client = makeClient({
      findSimilar: vi.fn().mockReturnValueOnce(first.promise),
      searchImage: vi.fn().mockReturnValueOnce(second.promise),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    await clickImageSearch();
    expect(client.findSimilar).toHaveBeenCalledWith(CAT_ID);
    expect(client.searchImage).toHaveBeenCalledTimes(1);
    expect(client.cancelSearch).not.toHaveBeenCalled();

    await act(async () => {
      first.reject({ error: "SidecarError", detail: "older request failed", retryable: true });
    });

    const pending = screen.getByRole("status", { name: "Image search results" });
    expect(pending).toHaveTextContent("Searching the Active Index Recipe for the chosen image…");
    expect(screen.queryByRole("alert", { name: "Image search error" })).not.toBeInTheDocument();

    await act(async () => {
      second.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query_path: "C:/Source/query.png",
        query_media_type: "image/png",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.92 })],
      } as never);
    });
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Image search results" })).toHaveTextContent("Image results · 1");
    });
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);
  });

  it("a late similar success cannot replace a newer request for the same Asset or finish it early", async () => {
    const first = deferred<Record<string, unknown>>();
    const second = deferred<Record<string, unknown>>();
    const client = makeClient({
      findSimilar: vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));

    await act(async () => {
      first.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.99 })],
      } as never);
    });

    const pending = screen.getByRole("status", { name: "Similar search results" });
    expect(pending).toHaveTextContent("Finding similar Assets…");
    expect(screen.queryByRole("alert", { name: "Similar search error" })).not.toBeInTheDocument();

    await act(async () => {
      second.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.92 })],
      } as never);
    });
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 1");
    });
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("current Asset cache removal and status changes suppress committed similar projections", async () => {
    let currentAssets: AssetListResult = assets;
    const client = makeClient({
      getAssets: vi.fn(async () => currentAssets),
      findSimilar: vi.fn(async () => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [
          searchAsset({ asset_id: ZEBRA_ID, score: 0.97 }),
          searchAsset({ asset_id: CAT_ID, score: 0.93 }),
        ],
      })),
    });
    const { queryClient } = renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 2");
    });
    expect(cardOrder()).toEqual(["Open zebra.png", "Open cat-meme.png"]);

    currentAssets = {
      ...assets,
      assets: assets.assets
        .filter((asset) => asset.asset_id !== ZEBRA_ID && asset.asset_id !== CAT_ID)
        .map((asset) => (asset.asset_id === DOG_ID ? { ...asset, status: "pending" } : asset)),
    };
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["assets"] });
    });

    expect(await screen.findByLabelText("No similar matches")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent("Similar results · 0");
    expect(screen.getByRole("status", { name: "Similar search results" })).toHaveTextContent(`Source Asset: “${CAT_ID}”`);
    expect(screen.getByText(/2 similar results.*omitted/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /zebra\.png/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cat-meme\.png/ })).not.toBeInTheDocument();
  });

  it("clearing a pending similar search never sends a backend cancellation", async () => {
    const gate = deferred<Record<string, unknown>>();
    const client = makeClient({
      findSimilar: vi.fn(async () => gate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    const section = await screen.findByRole("status", { name: "Similar search results" });
    fireEvent.click(within(section).getByRole("button", { name: "Clear search" }));

    expect(client.cancelSearch).not.toHaveBeenCalled();
    expect(screen.queryByRole("status", { name: "Similar search results" })).not.toBeInTheDocument();
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);

    await act(async () => {
      gate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
      } as never);
    });
    await flush();
    expect(screen.queryByRole("status", { name: "Similar search results" })).not.toBeInTheDocument();
    expect(client.cancelSearch).not.toHaveBeenCalled();
  });

  it("unmounting a pending similar search never sends a backend cancellation", async () => {
    const gate = deferred<Record<string, unknown>>();
    const client = makeClient({
      findSimilar: vi.fn(async () => gate.promise as never),
    });
    const { unmount } = renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    fireEvent.click(screen.getByRole("button", { name: `Find Similar for asset ${CAT_ID}` }));
    await screen.findByRole("status", { name: "Similar search results" });
    unmount();

    expect(client.cancelSearch).not.toHaveBeenCalled();
    await act(async () => {
      gate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        asset_id: CAT_ID,
        top_k: 18,
        results: [],
      } as never);
    });
    await flush();
    expect(client.cancelSearch).not.toHaveBeenCalled();
  });

  it("a picker resolving after Library unmount never starts image search", async () => {
    const picker = deferred<{ selected_path: string | null }>();
    const client = makeClient({
      chooseSearchImage: vi.fn(async () => picker.promise),
    });
    const { unmount } = renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    expect(client.chooseSearchImage).toHaveBeenCalledTimes(1);
    unmount();

    await act(async () => {
      picker.resolve({ selected_path: "C:/Source/query.png" });
    });
    await flush();
    expect(client.searchImage).not.toHaveBeenCalled();
    expect(client.cancelSearch).not.toHaveBeenCalled();
  });

  it("a new image selection falls back to Library browsing while pending and after failure", async () => {
    const second = deferred<never>();
    const client = makeClient({
      searchImage: vi
        .fn()
        .mockResolvedValueOnce({
          library_root: "C:/Library",
          active_recipe_id: "recipe-1",
          active_recipe_label: "Vulkan0 recipe",
          query_path: "C:/Source/first-query.png",
          query_media_type: "image/png",
          top_k: 18,
          results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.93 })],
        })
        .mockReturnValueOnce(second.promise),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await clickImageSearch();
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Image search results" })).toHaveTextContent("Image results · 1");
    });
    expect(cardOrder()).toEqual(["Open zebra.png"]);

    await clickImageSearch();
    const pending = await screen.findByRole("status", { name: "Image search results" });
    expect(pending).toHaveTextContent("Searching the Active Index Recipe for the chosen image…");
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);

    await act(async () => {
      second.reject({ error: "SidecarError", detail: "second image search failed", retryable: true });
    });

    expect(await screen.findByRole("alert", { name: "Image search error" })).toHaveTextContent("second image search failed");
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);
  });
});
