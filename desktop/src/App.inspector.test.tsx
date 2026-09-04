import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { App } from "./App";
import { AssetInspector } from "./features/assets/AssetInspector";
import type { AssetDetail, AssetListResult } from "./api/types";
import { importSnapshot } from "./features/import/import-test-fixtures";
import { resetRuntimeHealthForTesting } from "./features/runtime/runtimeHealthStore";

const FIRST_ASSET = "123e4567-e89b-12d3-a456-426614174000";
const SECOND_ASSET = "123e4567-e89b-12d3-a456-426614174002";

const assets: AssetListResult = {
  library_root: "C:/Library",
  active_recipe_id: "recipe-1",
  active_recipe_label: "Vulkan0 recipe",
  assets: [
    {
      asset_id: FIRST_ASSET,
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
      asset_id: SECOND_ASSET,
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
    ocr_results: [
      {
        result_id: "ocr-1",
        text: "reaction text",
        confidence: 0.9,
        language_hint: "en",
        created_at: "2026-08-09T00:00:00Z",
      },
    ],
    renditions: [],
    jobs: [
      {
        job_id: "job-1",
        type: "embed_asset",
        status: "pending",
        recipe_id: "recipe-1",
        attempt_count: 0,
      },
    ],
  };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  return {
    getAppState: async () => ({
      library_root: "C:/Library",
      runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
      setup_state: { health_check_ok: true },
      library_status: { total_assets: 2, job_counts: { pending: 0 } },
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
    revealAsset: vi.fn(async () => undefined),
    openLogDirectory: async () => undefined,
    deleteAsset: vi.fn(async (assetId: string) => ({
      library_root: "C:/Library",
      asset_id: assetId,
      removed_source_path: null,
      asset_deleted: true,
      removed_source_records: 1,
      removed_jobs: 1,
      removed_renditions: 1,
      removed_embeddings: 1,
    })),
    removeSourceRecord: vi.fn(async (assetId: string, sourcePath: string) => ({
      library_root: "C:/Library",
      asset_id: assetId,
      removed_source_path: sourcePath,
      asset_deleted: false,
      removed_source_records: 1,
      removed_jobs: 0,
      removed_renditions: 0,
      removed_embeddings: 0,
    })),
    batchAssetAction: vi.fn(async () => {
      throw new Error("not under test");
    }),
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
    searchText: async () => {
      throw new Error("not under test");
    },
    searchImage: async () => {
      throw new Error("not under test");
    },
    findSimilar: vi.fn(async () => {
      throw new Error("not under test");
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
    cancelSearch: async (requestId: string) => ({
      request_id: requestId,
      cancelled: true,
      was_active: true,
    }),
    copyAssetToClipboard: vi.fn(async () => undefined),
    copyOriginalFile: vi.fn(async () => undefined),
    copyOriginalFiles: vi.fn(async () => undefined),
    acceptDuplicatePair: async () => {
      throw new Error("not under test");
    },
    clearAcceptedPairs: async () => {
      throw new Error("not under test");
    },
    ...overrides,
  };
}

function HistoryCapture({ onCapture }: { onCapture: (loc: { pathname: string; search: string }) => void }) {
  const location = useLocation();
  onCapture({ pathname: location.pathname, search: location.search });
  return null;
}

function renderApp(route: string | string[], client: ReturnType<typeof makeClient>, initialIndex?: number) {
  const entries = Array.isArray(route) ? route : [route];
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let testLocation = { pathname: "", search: "" };
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
        <HistoryCapture onCapture={(loc) => (testLocation = loc)} />
        <App client={client as never} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, getLocation: () => testLocation, queryClient };
}

async function openInspectorSections() {
  // Advanced + overflow start collapsed (ticket 10). Open both so their
  // buttons/regions become queryable in jsdom.
  const advanced = await screen.findByRole("button", { name: "Advanced" });
  fireEvent.click(advanced);
  const more = await screen.findByRole("button", { name: "More actions" });
  fireEvent.click(more);
}

describe("Inspector and Clipboard Copy UI (ticket 10)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRuntimeHealthForTesting();
    vi.clearAllMocks();
  });

  it("clicking a card updates asset and opens a non-overlaying right panel with the waterfall still mounted", async () => {
    const client = makeClient();
    const { container, getLocation } = renderApp("/", client);

    await screen.findByText("Pending Asset");
    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));

    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    expect(inspector).toBeInTheDocument();
    expect(inspector.tagName.toLowerCase()).toBe("aside");
    expect(screen.queryByRole("dialog", { name: "Asset details" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
    expect(getLocation().search).toContain(`asset=${FIRST_ASSET}`);
    expect(container.querySelector(".library-body")).toHaveAttribute("data-inspector", "open");
    expect(container.querySelector(".library-content + .library-inspector")).not.toBeNull();
  });

  it("deep-links the inspector from the URL on first load and closes by removing only asset", async () => {
    const client = makeClient();
    // Ticket 11: `q` now filters the waterfall locally, so use a matching
    // query (`first`) to keep the card visible while asserting inspector URL behavior.
    const { getLocation } = renderApp(`/?q=first&sort=oldest&asset=${FIRST_ASSET}`, client);

    expect(await screen.findByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
    const search = getLocation().search;
    expect(search).toContain("q=first");
    expect(search).toContain("sort=oldest");
    expect(search).not.toContain("asset=");
  });

  it("supports browser back/forward for the inspector while preserving Library state", async () => {
    const client = makeClient();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function BackForward() {
      const navigate = useNavigate();
      return (
        <div>
          <button type="button" onClick={() => navigate(-1)}>
            Go back
          </button>
          <button type="button" onClick={() => navigate(1)}>
            Go forward
          </button>
        </div>
      );
    }
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/", `/?asset=${FIRST_ASSET}`]} initialIndex={1}>
          <BackForward />
          <App client={client as never} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go back" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go forward" }));
    expect(await screen.findByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("renders all required sections: primary, secondary, collapsed advanced, and overflow", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    const inspectorAside = await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy to Clipboard" });
    expect(
      within(inspectorAside).getByRole("img", { name: "first.gif preview" }),
    ).toHaveAttribute(
      "src",
      "http://memesort-media.localhost/media/originals/first.gif",
    );
    expect(screen.getByRole("button", { name: "Find Similar" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Reveal in Explorer" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("region", { name: "Details" })).toHaveTextContent("320 × 180");
    expect(screen.getByRole("region", { name: "OCR" })).toHaveTextContent("reaction text");
    expect(screen.getByRole("region", { name: "Source Records" })).toHaveTextContent("C:/Source/first.gif");

    await openInspectorSections();
    expect(screen.getByRole("region", { name: "Active Index Recipe" })).toHaveTextContent("Vulkan0 recipe");
    expect(screen.getByRole("region", { name: "Jobs" })).toHaveTextContent("embed_asset");
    expect(screen.getByRole("button", { name: "Copy original file" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Asset" })).toBeInTheDocument();
  });

  it("Clipboard Copy passes only the selected Asset ID and keeps the inspector open with selection intact", async () => {
    const client = makeClient();
    renderApp("/", client);

    fireEvent.click(await screen.findByLabelText("Select first.gif"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));
    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(await screen.findByRole("button", { name: "Copy to Clipboard" }));
    expect(await screen.findByText("Copied to clipboard. Paste into QQ or WeChat.")).toBeInTheDocument();

    expect(client.copyAssetToClipboard).toHaveBeenCalledTimes(1);
    expect(client.copyAssetToClipboard).toHaveBeenCalledWith(FIRST_ASSET);
    const [passed] = (client.copyAssetToClipboard as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(typeof passed).toBe("string");
    expect(passed).not.toContain("/");
    expect(passed).not.toContain("C:");

    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect((screen.getByLabelText("Select first.gif") as HTMLInputElement).checked).toBe(true);
  });

  it("Copy original file calls the raw Library Copy reference command with the Asset ID only", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy to Clipboard" });
    await openInspectorSections();
    fireEvent.click(screen.getByRole("button", { name: "Copy original file" }));

    expect(await screen.findByText("Original file reference copied.")).toBeInTheDocument();
    expect(client.copyOriginalFile).toHaveBeenCalledTimes(1);
    expect(client.copyOriginalFile).toHaveBeenCalledWith(FIRST_ASSET);
    const [passed] = (client.copyOriginalFile as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(passed).not.toContain("/");
    expect(passed).not.toContain("C:");
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("Copy failure leaves browsing usable and offers Reveal in Explorer without claiming rollback", async () => {
    const client = makeClient({
      copyAssetToClipboard: vi.fn(async () => {
        throw { error: "SidecarError", detail: "Clipboard is busy.", retryable: true };
      }),
    });
    renderApp("/", client);

    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));
    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(await screen.findByRole("button", { name: "Copy to Clipboard" }));
    const alert = await screen.findByRole("alert", { name: "Clipboard Copy failed" });
    expect(alert).toHaveTextContent("Clipboard is busy.");
    expect(alert).not.toHaveTextContent(/rollback/i);
    const reveals = screen.getAllByRole("button", { name: "Reveal in Explorer" });
    expect(reveals.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(reveals[reveals.length - 1]);
    expect(await screen.findByText("Opened the managed Library Copy in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "managed");

    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("Find Similar exposes the ticket 12 action point with the Asset ID", async () => {
    const onFindSimilar = vi.fn();
    const client = makeClient();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <AssetInspector
            assetId={FIRST_ASSET}
            client={client as never}
            onClose={() => undefined}
            onFindSimilar={onFindSimilar}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Find Similar" }));
    expect(onFindSimilar).toHaveBeenCalledTimes(1);
    expect(onFindSimilar).toHaveBeenCalledWith(FIRST_ASSET);
    unmount();

    const appClient = makeClient();
    const appRender = renderApp(`/?asset=${FIRST_ASSET}`, appClient);
    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy to Clipboard" });
    expect(screen.getByRole("button", { name: "Find Similar" })).toBeInTheDocument();
    appRender.unmount();
  });

  it("Delete requires confirmation, calls deleteAsset with the ID, removes the Asset, and closes its inspector", async () => {
    let currentAssets = [...assets.assets];
    const client = makeClient({
      getAssets: async () => ({ ...assets, assets: [...currentAssets] }),
    });
    // Make the mocked Library reflect the deletion so the refetch after
    // invalidateQueries removes the Asset from the visible wall (the real
    // backend would no longer return it).
    (client.deleteAsset as ReturnType<typeof vi.fn>).mockImplementation(
      async (assetId: string) => {
        currentAssets = currentAssets.filter((a) => a.asset_id !== assetId);
        return {
          library_root: "C:/Library",
          asset_id: assetId,
          removed_source_path: null,
          asset_deleted: true,
          removed_source_records: 1,
          removed_jobs: 1,
          removed_renditions: 1,
          removed_embeddings: 1,
        };
      },
    );
    const { getLocation } = renderApp("/", client);

    fireEvent.click(await screen.findByRole("button", { name: /first\.gif/i }));
    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy to Clipboard" });
    await openInspectorSections();

    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    expect(screen.getByRole("alertdialog", { name: "Delete this Asset?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument();
    expect(client.deleteAsset).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this Asset?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Asset" }));

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(client.deleteAsset).toHaveBeenCalledWith(FIRST_ASSET);
    const [passed] = (client.deleteAsset as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(passed).not.toContain("/");

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    expect(getLocation().search).not.toContain("asset=");
    expect(screen.queryByRole("button", { name: /first\.gif/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /indexed\.png/i })).toBeInTheDocument();
  });

  it("Reveal in Explorer passes only the Asset ID and recorded Source Paths come from server data", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy to Clipboard" });
    fireEvent.click(screen.getAllByRole("button", { name: "Reveal in Explorer" })[0]);
    expect(await screen.findByText("Opened the managed Library Copy in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "managed");

    fireEvent.click(screen.getByRole("button", { name: "Reveal Source" }));
    expect(await screen.findByText("Opened the recorded Source Path in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "source", "C:/Source/first.gif");
  });

  it("renders no centered detail dialog anywhere: the inspector is the only detail surface", async () => {
    const appClient = makeClient();
    const appRender = renderApp(`/?asset=${FIRST_ASSET}`, appClient);
    await screen.findByRole("complementary", { name: "Inspector" });
    expect(screen.queryByRole("dialog", { name: "Asset details" })).not.toBeInTheDocument();
    appRender.unmount();
  });
});

describe("Asset right-click copy menu (ticket 01 follow-up)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetRuntimeHealthForTesting();
    vi.clearAllMocks();
  });

  it("wall card right-click routes Copy image through copyAssetToClipboard with the Asset ID", async () => {
    const client = makeClient();
    const { container } = renderApp("/", client);
    await screen.findByRole("button", { name: /first\.gif/i });

    const card = container.querySelector(
      `article[data-asset-id="${FIRST_ASSET}"]`,
    ) as HTMLElement;
    // Native image menu suppressed (defaultPrevented).
    expect(
      fireEvent.contextMenu(card, { button: 2, clientX: 120, clientY: 90 }),
    ).toBe(false);

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy image" }));
    expect(client.copyAssetToClipboard).toHaveBeenCalledTimes(1);
    expect(client.copyAssetToClipboard).toHaveBeenCalledWith(FIRST_ASSET);
    const [passed] = (client.copyAssetToClipboard as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(typeof passed).toBe("string");
    expect(passed).not.toContain("/");
    expect(passed).not.toContain("C:");
    expect(await screen.findByText("Copied to clipboard. Paste into QQ or WeChat.")).toBeInTheDocument();
  });

  it("wall card right-click routes Copy original file through copyOriginalFile with the Asset ID", async () => {
    const client = makeClient();
    const { container } = renderApp("/", client);
    await screen.findByRole("button", { name: /first\.gif/i });

    const card = container.querySelector(
      `article[data-asset-id="${FIRST_ASSET}"]`,
    ) as HTMLElement;
    fireEvent.contextMenu(card, { button: 2, clientX: 120, clientY: 90 });

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy original file" }));
    expect(client.copyOriginalFile).toHaveBeenCalledTimes(1);
    expect(client.copyOriginalFile).toHaveBeenCalledWith(FIRST_ASSET);
    expect(await screen.findByText("Original file reference copied.")).toBeInTheDocument();
  });

  it("inspector preview right-click offers the same native Copy image action", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);
    const inspectorAside = await screen.findByRole("complementary", { name: "Inspector" });
    const preview = within(inspectorAside).getByRole("img", { name: "first.gif preview" });

    expect(
      fireEvent.contextMenu(preview, { button: 2, clientX: 200, clientY: 150 }),
    ).toBe(false);

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy image" }));
    expect(client.copyAssetToClipboard).toHaveBeenCalledTimes(1);
    expect(client.copyAssetToClipboard).toHaveBeenCalledWith(FIRST_ASSET);
    expect(await screen.findByText("Copied to clipboard. Paste into QQ or WeChat.")).toBeInTheDocument();
  });
});
