import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("clicking a card keeps the waterfall mounted while opening the single responsive inspector", async () => {
    const client = makeClient();
    const { container, getLocation } = renderApp("/", client);

    await screen.findByText("Pending Asset");
    const firstCard = container.querySelector<HTMLElement>(
      `article[data-asset-id="${FIRST_ASSET}"]`,
    );
    expect(firstCard).not.toBeNull();
    expect(within(firstCard as HTMLElement).queryByRole("button", { name: "View" })).not.toBeInTheDocument();
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

  it("moves focus into the inspector when it opens", async () => {
    const client = makeClient();
    renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);

    const close = await screen.findByRole("button", { name: "Close inspector" });
    expect(close).toHaveFocus();
  });

  it("returns focus to the Asset trigger after closing the inspector", async () => {
    const client = makeClient();
    renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });

    const close = screen.getByRole("button", { name: "Close inspector" });
    close.focus();
    fireEvent.click(close);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /first\.gif/i })).toHaveFocus();
    });
  });

  it("does not move Library focus when Escape closes an untouched inspector", async () => {
    const client = makeClient();
    renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    const libraryTarget = screen.getByRole("button", { name: /indexed\.png/i });
    libraryTarget.focus();

    fireEvent.keyDown(window, { key: "Escape" });
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    expect(document.activeElement).toBe(libraryTarget);
  });

  it("lets Escape close an open context menu before it closes the inspector", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    const preview = within(inspector).getByRole("img", { name: "first.gif preview" });
    fireEvent.contextMenu(preview, { button: 2, clientX: 200, clientY: 150 });
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("restores context-menu focus before a second Escape closes the inspector", async () => {
    const client = makeClient();
    renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    const close = screen.getByRole("button", { name: "Close inspector" });
    expect(close).toHaveFocus();
    fireEvent.contextMenu(await within(inspector).findByRole("img", { name: "first.gif preview" }), {
      button: 2,
      clientX: 200,
      clientY: 150,
    });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(close).toHaveFocus());
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /first\.gif/i })).toHaveFocus();
    });
  });

  it("does not restore a dismissed menu opener over another Asset", async () => {
    const client = makeClient();
    const { container } = renderApp("/", client);

    const firstOpener = await screen.findByRole("button", { name: /first\.gif/i });
    const secondOpener = await screen.findByRole("button", { name: /indexed\.png/i });
    const firstCard = container.querySelector<HTMLElement>(
      `article[data-asset-id="${FIRST_ASSET}"]`,
    );
    expect(firstCard).not.toBeNull();

    firstOpener.focus();
    fireEvent.contextMenu(firstCard as HTMLElement, {
      button: 2,
      clientX: 200,
      clientY: 150,
    });
    expect(screen.getByRole("menu", { name: "Actions for first.gif" })).toBeInTheDocument();

    secondOpener.focus();
    fireEvent.pointerDown(secondOpener);
    expect(screen.queryByRole("menu", { name: "Actions for first.gif" })).not.toBeInTheDocument();
    expect(secondOpener).toHaveFocus();

    fireEvent.click(secondOpener);
    await screen.findByRole("complementary", { name: "Inspector" });
    const inspector = await screen.findByRole("region", { name: "Asset inspector" });
    expect(inspector).toHaveAttribute("data-asset-id", SECOND_ASSET);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Close inspector" })).toHaveFocus();
    });
  });

  it("closes the Import menu before the inspector on Escape", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    const importTrigger = screen.getByRole("button", { name: "Import" });
    fireEvent.click(importTrigger);
    const importItem = await screen.findByRole("menuitem", { name: "Choose Files" });
    importItem.focus();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("menu", { name: "Import options" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(importTrigger).toHaveFocus();
  });

  it("closes only one nested menu or panel for each Escape", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    const inspector = await screen.findByRole("complementary", { name: "Inspector" });
    const importTrigger = screen.getByRole("button", { name: "Import" });
    fireEvent.click(importTrigger);
    const preview = within(inspector).getByRole("img", { name: "first.gif preview" });
    fireEvent.contextMenu(preview, { button: 2, clientX: 200, clientY: 150 });
    expect(screen.getAllByRole("menu")).toHaveLength(2);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Actions for first.gif" })).not.toBeInTheDocument();
    expect(screen.getByRole("menu", { name: "Import options" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Import options" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();

    await openInspectorSections();
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    expect(screen.getByRole("alertdialog", { name: "Delete this Asset?" })).toBeInTheDocument();
    fireEvent.click(importTrigger);
    expect(screen.getByRole("menu", { name: "Import options" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    const importMenuOpen = screen.queryByRole("menu", { name: "Import options" }) !== null;
    const confirmationOpen = screen.queryByRole("alertdialog", { name: "Delete this Asset?" }) !== null;
    expect(importMenuOpen).not.toBe(confirmationOpen);
    expect(importMenuOpen).toBe(false);
    expect(confirmationOpen).toBe(true);
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Import options" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    });
  });

  it("restores confirmation focus before a second Escape closes the inspector", async () => {
    const client = makeClient();
    renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    await openInspectorSections();
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    await screen.findByRole("alertdialog", { name: "Delete this Asset?" });

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete Asset" })).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete this Asset?" });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete Asset" })).toHaveFocus());
    expect(dialog).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
      expect(opener).toHaveFocus();
    });
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

  it("restores the Asset opener when history closes the inspector", async () => {
    const client = makeClient();
    let goBack: (() => void) | null = null;
    function HistoryBack() {
      const navigate = useNavigate();
      goBack = () => navigate(-1);
      return null;
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <HistoryBack />
          <App client={client as never} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    screen.getByRole("button", { name: "Close inspector" }).focus();

    await act(async () => {
      goBack?.();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("does not restore Asset A focus after navigation changes the inspector to Asset B", async () => {
    const client = makeClient();
    function OpenSecond() {
      const navigate = useNavigate();
      return (
        <button type="button" onClick={() => navigate(`/?asset=${SECOND_ASSET}`)}>
          Open second
        </button>
      );
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <OpenSecond />
          <App client={client as never} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByRole("button", { name: "Open second" }));
    await screen.findByRole("img", { name: "indexed.png preview" });

    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
      expect(document.activeElement).toBe(screen.getByRole("region", { name: "Library workspace" }).querySelector(".library-content"));
    });
    expect(document.activeElement).not.toBe(opener);
  });

  it("renders all required sections: primary, secondary, collapsed advanced, and overflow", async () => {
    const client = makeClient();
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    const inspectorAside = await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy image" });
    expect(
      within(inspectorAside).getByRole("img", { name: "first.gif preview" }),
    ).toHaveAttribute(
      "src",
      "http://memesort-media.localhost/media/originals/first.gif",
    );
    const primary = within(inspectorAside).getByRole("region", { name: "Primary actions" });
    const preview = within(primary).getByRole("img", { name: "first.gif preview" });
    const copy = within(primary).getByRole("button", { name: "Copy image" });
    const similar = within(primary).getByRole("button", { name: "Find Similar" });
    const metadata = within(primary).getByRole("heading", { name: "first.gif" });
    const isBefore = (first: Element, second: Element) =>
      Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(isBefore(preview, copy)).toBe(true);
    expect(isBefore(copy, similar)).toBe(true);
    fireEvent.click(copy);
    const feedback = await within(primary).findByRole("status");
    expect(isBefore(similar, feedback)).toBe(true);
    expect(isBefore(feedback, metadata)).toBe(true);
    expect(isBefore(similar, metadata)).toBe(true);
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
    fireEvent.click(await screen.findByRole("button", { name: "Copy image" }));
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
    await screen.findByRole("button", { name: "Copy image" });
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
    fireEvent.click(await screen.findByRole("button", { name: "Copy image" }));
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
    await screen.findByRole("button", { name: "Copy image" });
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
    await screen.findByRole("button", { name: "Copy image" });
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
    await screen.findByRole("button", { name: "Copy image" });
    fireEvent.click(screen.getAllByRole("button", { name: "Reveal in Explorer" })[0]);
    expect(await screen.findByText("Opened the managed Library Copy in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "managed");

    fireEvent.click(screen.getByRole("button", { name: "Reveal Source" }));
    expect(await screen.findByText("Opened the recorded Source Path in File Explorer.")).toBeInTheDocument();
    expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "source", "C:/Source/first.gif");
  });

  it("reports Source reveal failures as errors without claiming success", async () => {
    const client = makeClient({
      revealAsset: vi.fn(async (_assetId: string, target: string) => {
        if (target === "source") {
          throw {
            error: "SidecarError",
            detail: "Recorded Source Path is unavailable.",
            retryable: true,
          };
        }
      }),
    });
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByRole("button", { name: "Reveal Source" }));

    const alert = await screen.findByRole("alert", { name: "Asset action failed" });
    expect(alert).toHaveTextContent("Recorded Source Path is unavailable.");
    expect(screen.queryByText("Opened the recorded Source Path in File Explorer.")).not.toBeInTheDocument();
  });

  it("does not surface delayed managed reveal success after selecting another Asset", async () => {
    let releaseReveal!: () => void;
    const revealGate = new Promise<void>((resolve) => {
      releaseReveal = resolve;
    });
    const client = makeClient({
      revealAsset: vi.fn(async (assetId: string, target: string) => {
        if (assetId === FIRST_ASSET && target === "managed") await revealGate;
      }),
    });
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByRole("button", { name: "Reveal in Explorer" }));
    await waitFor(() => {
      expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "managed");
    });

    fireEvent.click(screen.getByRole("button", { name: /indexed\.png/i }));
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      releaseReveal();
    });

    expect(screen.queryByText("Opened the managed Library Copy in File Explorer.")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("does not surface delayed managed reveal failure after selecting another Asset", async () => {
    let rejectReveal!: (reason: unknown) => void;
    const revealGate = new Promise<void>((_resolve, reject) => {
      rejectReveal = reject;
    });
    const client = makeClient({
      revealAsset: vi.fn(async (assetId: string, target: string) => {
        if (assetId === FIRST_ASSET && target === "managed") {
          await revealGate;
        }
      }),
    });
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByRole("button", { name: "Reveal in Explorer" }));
    await waitFor(() => {
      expect(client.revealAsset).toHaveBeenCalledWith(FIRST_ASSET, "managed");
    });

    fireEvent.click(screen.getByRole("button", { name: /indexed\.png/i }));
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      rejectReveal({
        error: "SidecarError",
        detail: "Managed Library Copy is unavailable.",
        retryable: true,
      });
    });

    expect(screen.queryByText("Managed Library Copy is unavailable.")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("renders no centered detail dialog anywhere: the inspector is the only detail surface", async () => {
    const appClient = makeClient();
    const appRender = renderApp(`/?asset=${FIRST_ASSET}`, appClient);
    await screen.findByRole("complementary", { name: "Inspector" });
    expect(screen.queryByRole("dialog", { name: "Asset details" })).not.toBeInTheDocument();
    appRender.unmount();
  });
});

describe("Source Record removal and deletion cache coordination", () => {
  const PRIMARY_SOURCE = "C:/Source/first.gif";
  const EXTRA_SOURCE = "C:/Source/first-extra.gif";

  beforeEach(() => {
    localStorage.clear();
    resetRuntimeHealthForTesting();
    vi.clearAllMocks();
  });

  function listWithSources(remaining: string[]) {
    return {
      ...assets,
      assets: assets.assets.map((asset) =>
        asset.asset_id === FIRST_ASSET
          ? { ...asset, source_record_count: remaining.length, source_records: remaining.map((source_path) => ({ source_path })) }
          : asset,
      ),
    };
  }

  function detailWithSources(assetId: string, remaining: string[]) {
    const base = detailFor(assetId);
    return {
      library_root: "C:/Library",
      active_recipe_id: "recipe-1",
      active_recipe_label: "Vulkan0 recipe",
      asset: assetId === FIRST_ASSET
        ? {
            ...base,
            source_record_count: remaining.length,
            source_records: remaining.map((source_path) => ({ source_path, imported_at: base.imported_at, last_seen_at: null })),
          }
        : base,
    };
  }

  function removeSourceResult(assetId: string, sourcePath: string, assetDeleted: boolean) {
    return {
      library_root: "C:/Library",
      asset_id: assetId,
      removed_source_path: sourcePath,
      asset_deleted: assetDeleted,
      removed_source_records: 1,
      removed_jobs: assetDeleted ? 1 : 0,
      removed_renditions: assetDeleted ? 1 : 0,
      removed_embeddings: assetDeleted ? 1 : 0,
    };
  }

  it("removing a non-final Source Record keeps the Asset, its selection, and shows the remaining Source Record", async () => {
    let remaining = [PRIMARY_SOURCE, EXTRA_SOURCE];
    const client = makeClient({
      getAssets: async () => listWithSources(remaining),
      getAssetDetail: async (assetId: string) => detailWithSources(assetId, remaining),
      removeSourceRecord: vi.fn(async (assetId: string, sourcePath: string) => {
        remaining = remaining.filter((path) => path !== sourcePath);
        return removeSourceResult(assetId, sourcePath, false);
      }),
    });
    const { container, getLocation } = renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(await screen.findByLabelText("Select first.gif"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    await screen.findByRole("complementary", { name: "Inspector" });

    fireEvent.click(screen.getAllByRole("button", { name: "Remove Source Record" })[0]);
    const dialog = await screen.findByRole("alertdialog", { name: "Remove this Source Record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove Source Record" }));
    expect(await screen.findByText("Removed the Source Record.")).toBeInTheDocument();

    // The Asset, its selection and its detail survive a normal removal.
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(getLocation().search).toContain(`asset=${FIRST_ASSET}`);
    // The Asset is still on the wall and still selected. Its displayed name
    // follows the remaining Source Record, so assert on the Asset itself.
    const card = container.querySelector(`article[data-asset-id="${FIRST_ASSET}"]`);
    expect(card).not.toBeNull();
    expect(card?.querySelector<HTMLInputElement>("input[type='checkbox']")?.checked).toBe(true);
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    const sources = screen.getByRole("region", { name: "Source Records" });
    expect(sources).toHaveTextContent(EXTRA_SOURCE);
    expect(sources).not.toHaveTextContent(PRIMARY_SOURCE);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Close inspector" })).toHaveFocus();
    });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
      expect(opener).toHaveFocus();
    });
  });

  it("reports Source Record removal failures as errors without dropping the Asset", async () => {
    const client = makeClient({
      removeSourceRecord: vi.fn(async () => {
        throw {
          error: "SidecarError",
          detail: "Source Record is locked.",
          retryable: true,
        };
      }),
    });
    renderApp("/", client);

    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    const removeButton = await screen.findByRole("button", { name: "Remove Source Record" });
    fireEvent.click(removeButton);
    const dialog = await screen.findByRole("alertdialog", { name: "Remove this Source Record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove Source Record" }));

    const alert = await screen.findByRole("alert", { name: "Asset action failed" });
    expect(alert).toHaveTextContent("Source Record is locked.");
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
    await waitFor(() => expect(removeButton).toHaveFocus());
  });

  it("does not surface delayed Source Record removal success after selecting another Asset", async () => {
    const removalResult = {
      library_root: "C:/Library",
      asset_id: FIRST_ASSET,
      removed_source_path: PRIMARY_SOURCE,
      asset_deleted: false,
      removed_source_records: 1,
      removed_jobs: 0,
      removed_renditions: 0,
      removed_embeddings: 0,
    };
    let releaseRemoval!: (result: typeof removalResult) => void;
    const removalGate = new Promise<typeof removalResult>((resolve) => {
      releaseRemoval = resolve;
    });
    const client = makeClient({
      removeSourceRecord: vi.fn(async () => removalGate),
    });
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByRole("button", { name: "Remove Source Record" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove this Source Record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove Source Record" }));
    await waitFor(() => {
      expect(client.removeSourceRecord).toHaveBeenCalledWith(FIRST_ASSET, PRIMARY_SOURCE);
    });

    fireEvent.click(screen.getByRole("button", { name: /indexed\.png/i }));
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      releaseRemoval(removalResult);
    });

    expect(screen.queryByText("Removed the Source Record.")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("calls onDeleted for delayed final Source Record removal after switching Assets", async () => {
    const removalResult = {
      library_root: "C:/Library",
      asset_id: FIRST_ASSET,
      removed_source_path: PRIMARY_SOURCE,
      asset_deleted: true,
      removed_source_records: 1,
      removed_jobs: 1,
      removed_renditions: 1,
      removed_embeddings: 1,
    };
    let releaseRemoval!: () => void;
    const removalGate = new Promise<typeof removalResult>((resolve) => {
      releaseRemoval = () => resolve(removalResult);
    });
    const client = makeClient({
      removeSourceRecord: vi.fn(async () => removalGate),
    });
    const onDeleted = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <AssetInspector
            assetId={FIRST_ASSET}
            client={client as never}
            onClose={() => undefined}
            onDeleted={onDeleted}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole("region", { name: "Asset inspector" });
    fireEvent.click(await screen.findByRole("button", { name: "Remove Source Record" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove this Source Record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove Source Record" }));
    await waitFor(() => {
      expect(client.removeSourceRecord).toHaveBeenCalledWith(FIRST_ASSET, PRIMARY_SOURCE);
    });

    rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <AssetInspector
            assetId={SECOND_ASSET}
            client={client as never}
            onClose={() => undefined}
            onDeleted={onDeleted}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      releaseRemoval();
    });

    await waitFor(() => {
      expect(onDeleted).toHaveBeenCalledWith(FIRST_ASSET);
    });
    expect(screen.getByRole("region", { name: "Asset inspector" })).toBeInTheDocument();
    expect(screen.queryByText("Removed the final Source Record and deleted the Orphan Asset.")).not.toBeInTheDocument();
  });

  it("does not surface delayed Source Record removal failure after selecting another Asset", async () => {
    let rejectRemoval!: (reason: unknown) => void;
    const removalGate = new Promise<never>((_resolve, reject) => {
      rejectRemoval = reject;
    });
    const client = makeClient({
      removeSourceRecord: vi.fn(async () => removalGate),
    });
    renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    fireEvent.click(screen.getByRole("button", { name: "Remove Source Record" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove this Source Record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove Source Record" }));
    await waitFor(() => {
      expect(client.removeSourceRecord).toHaveBeenCalledWith(FIRST_ASSET, PRIMARY_SOURCE);
    });

    fireEvent.click(screen.getByRole("button", { name: /indexed\.png/i }));
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      rejectRemoval({
        error: "SidecarError",
        detail: "Source Record is locked.",
        retryable: true,
      });
    });

    expect(screen.queryByText("Source Record is locked.")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("removing the final Source Record deletes the Orphan Asset like an explicit delete", async () => {
    let currentAssets = [...assets.assets];
    const getAssetDetail = vi.fn(async (assetId: string) => ({ library_root: "C:/Library", active_recipe_id: "recipe-1", active_recipe_label: "Vulkan0 recipe", asset: detailFor(assetId) }));
    const client = makeClient({
      getAssets: async () => ({ ...assets, assets: [...currentAssets] }),
      getAssetDetail,
      removeSourceRecord: vi.fn(async (assetId: string, sourcePath: string) => {
        currentAssets = currentAssets.filter((asset) => asset.asset_id !== assetId);
        return removeSourceResult(assetId, sourcePath, true);
      }),
    });
    const { getLocation } = renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    await waitFor(() => {
      expect(getAssetDetail.mock.calls.filter((call) => call[0] === FIRST_ASSET)).toHaveLength(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Source Record" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove this Source Record?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove Source Record" }));

    // Same post-delete result as Delete: Asset gone from the wall, detail
    // closed, and no refresh of a detail the backend just deleted.
    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /first\.gif/i })).not.toBeInTheDocument();
    expect(getLocation().search).not.toContain("asset=");
    expect(screen.getByRole("button", { name: /indexed\.png/i })).toBeInTheDocument();
    expect(getAssetDetail.mock.calls.filter((call) => call[0] === FIRST_ASSET)).toHaveLength(1);
  });

  it("keeps the Asset and selection after a single-delete rejection", async () => {
    const getAssets = vi.fn(async () => assets);
    const client = makeClient({
      getAssets,
      deleteAsset: vi.fn(async () => {
        throw {
          error: "SidecarError",
          detail: "Asset is locked by a running job.",
          retryable: false,
        };
      }),
    });
    const { getLocation } = renderApp("/", client);

    fireEvent.click(await screen.findByLabelText("Select first.gif"));
    const opener = await screen.findByRole("button", { name: /first\.gif/i });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByRole("complementary", { name: "Inspector" });
    await openInspectorSections();
    const assetFetchesBeforeDelete = getAssets.mock.calls.length;

    const deleteButton = screen.getByRole("button", { name: "Delete Asset" });
    fireEvent.click(deleteButton);
    const dialog = screen.getByRole("alertdialog", { name: "Delete this Asset?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Asset" }));
    await waitFor(() => {
      expect(client.deleteAsset).toHaveBeenCalledWith(FIRST_ASSET);
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Asset is locked by a running job.");
    expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Select first.gif")).toBeChecked();
    expect(getLocation().search).toContain(`asset=${FIRST_ASSET}`);
    expect(getAssets.mock.calls.length).toBe(assetFetchesBeforeDelete);
    await waitFor(() => expect(deleteButton).toHaveFocus());
  });

  it("keeps a detail that was opened while a Delete was still in flight", async () => {
    let releaseDelete!: () => void;
    const deleteGate = new Promise<void>((resolve) => {
      releaseDelete = resolve;
    });
    let currentAssets = [...assets.assets];
    const client = makeClient({
      getAssets: async () => ({ ...assets, assets: [...currentAssets] }),
      deleteAsset: vi.fn(async (assetId: string) => {
        await deleteGate;
        currentAssets = currentAssets.filter((asset) => asset.asset_id !== assetId);
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
      }),
    });
    const { getLocation } = renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy image" });
    await openInspectorSections();
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this Asset?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Asset" }));

    // The user opens another Asset while the confirmed delete is still running.
    fireEvent.click(screen.getByRole("button", { name: /indexed\.png/i }));
    await waitFor(() => {
      expect(getLocation().search).toContain(`asset=${SECOND_ASSET}`);
    });

    await act(async () => {
      releaseDelete();
    });

    expect(client.deleteAsset).toHaveBeenCalledWith(FIRST_ASSET);
    // The newer detail is not closed by the older delete.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /first\.gif/i })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(getLocation().search).toContain(`asset=${SECOND_ASSET}`);
  });

  it("calls onDeleted for delayed single delete after switching Assets", async () => {
    const deletionResult = {
      library_root: "C:/Library",
      asset_id: FIRST_ASSET,
      removed_source_path: null,
      asset_deleted: true,
      removed_source_records: 1,
      removed_jobs: 1,
      removed_renditions: 1,
      removed_embeddings: 1,
    };
    let releaseDelete!: () => void;
    const deleteGate = new Promise<typeof deletionResult>((resolve) => {
      releaseDelete = () => resolve(deletionResult);
    });
    const client = makeClient({
      deleteAsset: vi.fn(async () => deleteGate),
    });
    const onDeleted = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <AssetInspector
            assetId={FIRST_ASSET}
            client={client as never}
            onClose={() => undefined}
            onDeleted={onDeleted}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole("region", { name: "Asset inspector" });
    await openInspectorSections();
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this Asset?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Asset" }));
    await waitFor(() => {
      expect(client.deleteAsset).toHaveBeenCalledWith(FIRST_ASSET);
    });

    rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <AssetInspector
            assetId={SECOND_ASSET}
            client={client as never}
            onClose={() => undefined}
            onDeleted={onDeleted}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      releaseDelete();
    });

    await waitFor(() => {
      expect(onDeleted).toHaveBeenCalledWith(FIRST_ASSET);
    });
    expect(screen.getByRole("region", { name: "Asset inspector" })).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog", { name: "Delete this Asset?" })).not.toBeInTheDocument();
  });

  it("does not surface delayed single-delete failure after selecting another Asset", async () => {
    let rejectDelete!: (reason: unknown) => void;
    const deleteGate = new Promise<never>((_resolve, reject) => {
      rejectDelete = reject;
    });
    const client = makeClient({
      deleteAsset: vi.fn(async () => deleteGate),
    });
    const { getLocation } = renderApp(`/?asset=${FIRST_ASSET}`, client);

    await screen.findByRole("complementary", { name: "Inspector" });
    await screen.findByRole("button", { name: "Copy image" });
    await openInspectorSections();
    fireEvent.click(screen.getByRole("button", { name: "Delete Asset" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this Asset?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete Asset" }));
    await waitFor(() => {
      expect(client.deleteAsset).toHaveBeenCalledWith(FIRST_ASSET);
    });

    fireEvent.click(screen.getByRole("button", { name: /indexed\.png/i }));
    await screen.findByRole("img", { name: "indexed.png preview" });
    await act(async () => {
      rejectDelete({
        error: "SidecarError",
        detail: "Asset deletion was refused.",
        retryable: false,
      });
    });

    expect(screen.queryByText("Asset deletion was refused.")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
    expect(getLocation().search).toContain(`asset=${SECOND_ASSET}`);
    expect(screen.getByRole("button", { name: /first\.gif/i })).toBeInTheDocument();
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
