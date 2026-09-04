import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import type { AssetListResult, SearchAsset } from "./api/types";
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
      throw new Error("searchText mock not configured");
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

async function submitSearch() {
  fireEvent.click(screen.getByRole("button", { name: /^Search/ }));
}

describe("Library local filtering and semantic text search (ticket 11)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRuntimeHealthForTesting();
    vi.clearAllMocks();
  });

  it("typing filters locally without calling searchText (case-insensitive name match)", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("CAT");
    expect(client.searchText).not.toHaveBeenCalled();
    expect(await screen.findByText(/Local matches for/)).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);
  });

  it("local matching covers the available/primary Source Path", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("dog-park");
    expect(client.searchText).not.toHaveBeenCalled();
    expect(cardOrder()).toEqual(["Open dog-park.png"]);
  });

  it("local filtering includes Pending and Failed Assets", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("dog");
    expect(cardOrder()).toEqual(["Open dog-park.png"]);

    await typeQuery("bird-photo");
    expect(cardOrder()).toEqual(["Open bird-photo.png"]);

    expect(screen.getByText(/includes Pending and Failed/i)).toBeInTheDocument();
    expect(client.searchText).not.toHaveBeenCalled();
  });

  it("local matching covers only documented name and Source Path data", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    // Status and media-type substrings must not match anything.
    await typeQuery("indexed");
    expect(await screen.findByText(/No local matches for/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Open / })).not.toBeInTheDocument();
    expect(client.searchText).not.toHaveBeenCalled();
  });

  it("a URL q recreates local filtering only and never runs semantic search", async () => {
    const client = makeClient();
    renderApp("/?q=dog", client);
    await screen.findByRole("button", { name: /dog-park\.png/ });

    expect(client.searchText).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Search Library") as HTMLInputElement).value).toBe("dog");
    expect(await screen.findByText(/Local matches for/)).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open dog-park.png"]);
  });

  it("submit starts exactly one UUID-scoped Search Request with in-progress state and keeps the Library on failure", async () => {
    const gate = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn(async () => gate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("cat");
    expect(client.searchText).not.toHaveBeenCalled();

    await submitSearch();
    expect(client.searchText).toHaveBeenCalledTimes(1);
    expect(client.searchText).toHaveBeenCalledWith("cat", expect.any(String));
    const requestId = (client.searchText as ReturnType<typeof vi.fn>).mock.calls[0][1] as string;
    expect(typeof requestId).toBe("string");
    expect(requestId.length).toBeGreaterThan(8);

    // In-progress without discarding local browsing.
    expect(await screen.findByText(/Searching the Active Index Recipe for/)).toBeInTheDocument();
    expect(screen.getAllByText(/Searching the Active Index Recipe/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: /cat-meme\.png/ })).toBeInTheDocument();

    await act(async () => {
      gate.reject({ error: "SidecarError", detail: "index unavailable", retryable: true });
    });
    expect(await screen.findByRole("alert", { name: "Semantic search error" })).toHaveTextContent(
      "index unavailable",
    );
    // Library remains usable through local matches.
    expect(screen.getByRole("button", { name: /cat-meme\.png/ })).toBeInTheDocument();
    expect(screen.getByText(/Library browsing remains available/)).toBeInTheDocument();
  });

  it("composed semantic results use the shared waterfall in relevance order, exclude non-Indexed, and hide scores except in advanced details", async () => {
    const client = makeClient({
      // Relevance order: zebra (0.91) > pending dog (0.85, excluded) > cat (0.82) > missing (0.70, stale).
      searchText: vi.fn(async () => ({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "meme",
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

    await typeQuery("meme");
    await submitSearch();

    // Semantic header with relevance count (2 indexed, pending excluded, missing stale).
    expect(await screen.findByText(/Semantic results for/)).toBeInTheDocument();
    // Relevance order preserved (zebra before cat), not newest-first (cat is newest).
    expect(cardOrder()).toEqual(["Open zebra.png", "Open cat-meme.png"]);
    // Pending dog excluded from semantic results.
    expect(screen.queryByRole("button", { name: /dog-park\.png/ })).not.toBeInTheDocument();
    // Stale diagnostic via ticket 05 composition.
    expect(screen.getByText(/1 semantic result.*omitted/i)).toBeInTheDocument();
    // Raw scores hidden outside advanced details.
    const details = screen.getByText("Advanced details").closest("details")!;
    expect(details).not.toBeNull();
    const scoreInDetails = within(details as HTMLElement).getByText(/score 0\.910/);
    expect(scoreInDetails).toBeInTheDocument();
    // No score text leaks outside the details element.
    const allScores = screen.getAllByText(/score 0\./);
    for (const node of allScores) {
      expect(details.contains(node)).toBe(true);
    }
  });

  it("a new submit cancels the previous waiting request", async () => {
    const first = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const second = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn().mockImplementationOnce(() => first.promise as never).mockImplementationOnce(() => second.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("first");
    await submitSearch();
    const firstId = (client.searchText as ReturnType<typeof vi.fn>).mock.calls[0][1] as string;

    await typeQuery("second");
    await submitSearch();
    expect(client.searchText).toHaveBeenCalledTimes(2);
    expect(client.cancelSearch).toHaveBeenCalledTimes(1);
    expect(client.cancelSearch).toHaveBeenCalledWith(firstId);

    await act(async () => {
      second.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "second",
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
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  });

  it("only the latest result wins when an older promise settles later", async () => {
    const first = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const second = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn().mockImplementationOnce(() => first.promise as never).mockImplementationOnce(() => second.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("first");
    await submitSearch();
    await typeQuery("second");
    await submitSearch();

    await act(async () => {
      second.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "second",
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.9 })],
      } as never);
    });
    expect(await screen.findByRole("button", { name: /zebra\.png/ })).toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);

    // Older completion must not overwrite the latest visible results.
    await act(async () => {
      first.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "first",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.95 })],
      } as never);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(cardOrder()).toEqual(["Open zebra.png"]);
    expect(screen.queryByRole("button", { name: /cat-meme\.png/ })).not.toBeInTheDocument();
  });

  it("only the latest error wins and older errors cannot overwrite fresh results", async () => {
    const first = deferred<never>();
    const second = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn().mockImplementationOnce(() => first.promise as never).mockImplementationOnce(() => second.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("first");
    await submitSearch();
    await typeQuery("second");
    await submitSearch();

    await act(async () => {
      second.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "second",
        top_k: 18,
        results: [searchAsset({ asset_id: ZEBRA_ID, score: 0.9 })],
      } as never);
    });
    expect(await screen.findByRole("button", { name: /zebra\.png/ })).toBeInTheDocument();

    await act(async () => {
      first.reject({ error: "SidecarError", detail: "stale failure", retryable: true });
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    // Stale error must not replace the latest results.
    expect(screen.queryByRole("alert", { name: "Semantic search error" })).not.toBeInTheDocument();
    expect(cardOrder()).toEqual(["Open zebra.png"]);
  });

  it("a latest error wins over an older late success", async () => {
    const first = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const second = deferred<never>();
    const client = makeClient({
      searchText: vi.fn().mockImplementationOnce(() => first.promise as never).mockImplementationOnce(() => second.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("first");
    await submitSearch();
    await typeQuery("second");
    await submitSearch();

    await act(async () => {
      second.reject({ error: "SidecarError", detail: "latest failed", retryable: true });
    });
    expect(await screen.findByRole("alert", { name: "Semantic search error" })).toHaveTextContent("latest failed");

    await act(async () => {
      first.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "first",
        top_k: 18,
        results: [searchAsset({ asset_id: CAT_ID, score: 0.99 })],
      } as never);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    // Older success must not overwrite the latest error feedback, and its
    // stale semantic card must never appear (fallback stays local for "second").
    expect(screen.getByRole("alert", { name: "Semantic search error" })).toHaveTextContent("latest failed");
    expect(screen.queryByRole("button", { name: /cat-meme\.png/ })).not.toBeInTheDocument();
    expect(screen.getByText(/No local matches for/)).toBeInTheDocument();
  });

  it("clear cancels active work and restores browsing with sort applied", async () => {
    const gate = deferred<never>();
    const client = makeClient({
      searchText: vi.fn(async () => gate.promise as never),
    });
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    // Default newest-first: cat, zebra, dog, bird.
    expect(cardOrder()).toEqual([
      "Open cat-meme.png",
      "Open zebra.png",
      "Open dog-park.png",
      "Open bird-photo.png",
    ]);

    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "oldest" } });
    expect(cardOrder()).toEqual([
      "Open bird-photo.png",
      "Open dog-park.png",
      "Open zebra.png",
      "Open cat-meme.png",
    ]);

    await typeQuery("cat");
    expect(cardOrder()).toEqual(["Open cat-meme.png"]);
    await submitSearch();
    const activeId = (client.searchText as ReturnType<typeof vi.fn>).mock.calls[0][1] as string;
    expect(await screen.findByText(/Searching the Active Index Recipe for/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(client.cancelSearch).toHaveBeenCalledWith(activeId);
    // Full list restored with the selected oldest sort.
    expect(await screen.findByRole("button", { name: /bird-photo\.png/ })).toBeInTheDocument();
    expect(cardOrder()).toEqual([
      "Open bird-photo.png",
      "Open dog-park.png",
      "Open zebra.png",
      "Open cat-meme.png",
    ]);
    expect((screen.getByLabelText("Search Library") as HTMLInputElement).value).toBe("");
    expect(client.searchText).toHaveBeenCalledTimes(1);
  });

  it("leaving Library cancels active work", async () => {
    const gate = deferred<{ results: SearchAsset[] } & Record<string, unknown>>();
    const client = makeClient({
      searchText: vi.fn(async () => gate.promise as never),
    });
    const { unmount } = renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    await typeQuery("cat");
    await submitSearch();
    const activeId = (client.searchText as ReturnType<typeof vi.fn>).mock.calls[0][1] as string;

    unmount();
    expect(client.cancelSearch).toHaveBeenCalledWith(activeId);
    await act(async () => {
      gate.resolve({
        library_root: "C:/Library",
        active_recipe_id: "recipe-1",
        active_recipe_label: "Vulkan0 recipe",
        query: "cat",
        top_k: 18,
        results: [],
      } as never);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  });

  it("empty submit never starts a Search Request", async () => {
    const client = makeClient();
    renderApp("/", client);
    await screen.findByRole("button", { name: /cat-meme\.png/ });

    expect(screen.getByRole("button", { name: /^Search/ })).toBeDisabled();
    expect(client.searchText).not.toHaveBeenCalled();
  });
});
