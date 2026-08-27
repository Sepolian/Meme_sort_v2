import { render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { AssetsWorkspace } from "./AssetsWorkspace";
import type { MemeSortClient } from "../../api/tauri-client";
import { ImportBatchContext } from "../import/ImportBatchContext";
import type {
  NativeDragListener,
  NativeDragSubscribe,
  NativeDragSummary,
} from "../../api/native-drag";
import type { AssetListResult, ImportTask } from "../../api/types";

function dragSummary(partial: Partial<NativeDragSummary>): NativeDragSummary {
  return {
    phase: "over",
    fileCount: 0,
    folderCount: 0,
    x: 10,
    y: 10,
    accepted: true,
    dropId: null,
    ...partial,
  };
}

class FakeNativeDrag {
  listeners = new Set<NativeDragListener>();
  subscriptionCount = 0;

  subscribe: NativeDragSubscribe = (listener) => {
    this.subscriptionCount += 1;
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  fire(summary: NativeDragSummary) {
    [...this.listeners].forEach((listener) => listener(summary));
  }

  get activeCount(): number {
    return this.listeners.size;
  }
}

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
  ],
};

const conflictError = {
  status: 409,
  error: "ImportBatchConflictError",
  detail: "An Import Batch is already running or paused.",
  retryable: false,
};

function createClient(overrides: Partial<MemeSortClient> = {}): MemeSortClient {
  return {
    getAssets: vi.fn(async () => assets),
    startLibraryImport: vi.fn(async () => ({
      status: "running",
      running: true,
      paused: false,
      pause_requested: false,
      source_folder: null,
      started_at: 1,
      finished_at: null,
      result: null,
      error: null,
    })),
    ...overrides,
  } as unknown as MemeSortClient;
}

function renderWorkspace(options: {
  client?: MemeSortClient;
  nativeDrag?: NativeDragSubscribe;
  list?: AssetListResult;
} = {}) {
  const client =
    options.client
    ?? createClient({
      getAssets: vi.fn(async () => options.list ?? assets),
    });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const startBatch = async (start: () => Promise<ImportTask>) => {
    const snapshot = await start();
    queryClient.setQueryData(["import-batch"], snapshot);
    return snapshot;
  };
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ImportBatchContext.Provider value={{
        snapshot: null,
        startBatch,
        requestPause: async () => undefined,
        requestResume: async () => undefined,
        controlsPending: false,
      }}>
        <AssetsWorkspace
          client={client}
          selectedAssetId={null}
          onSelectAsset={() => undefined}
          onCloseDetail={() => undefined}
          nativeDrag={options.nativeDrag}
        />
      </ImportBatchContext.Provider>
    </QueryClientProvider>,
  );
  return { client, ...view };
}

function stubWallRect(element: Element, left: number, top: number, right: number, bottom: number) {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
    x: left,
    y: top,
    toJSON: () => undefined,
  } as DOMRect);
}

async function renderedWall(label: string) {
  return await screen.findByLabelText(label);
}

describe("AssetsWorkspace native drag-and-drop", () => {
  it("keeps a persistent muted drag hint beside the chooser controls", async () => {
    renderWorkspace();

    expect(await screen.findByText(/drag image files or folders onto the asset wall/i)).toBeVisible();
  });

  it("shows a centered import card for an empty Library and starts one import per validated drop", async () => {
    const fake = new FakeNativeDrag();
    const list: AssetListResult = { ...assets, assets: [] };
    const { client } = renderWorkspace({ nativeDrag: fake.subscribe, list });

    const card = await renderedWall("Import drop card");
    stubWallRect(card, 100, 50, 500, 450);

    await act(async () => {
      fake.fire(
        dragSummary({ phase: "enter", fileCount: 1, folderCount: 1, x: 200, y: 120 }),
      );
    });
    expect(screen.getByRole("status", { name: /release to import/i })).toHaveTextContent("2 items ready");

    await act(async () => {
      fake.fire(
        dragSummary({
          phase: "drop",
          fileCount: 1,
          folderCount: 1,
          x: 200,
          y: 120,
          dropId: "123e4567-e89b-12d3-a456-426614174010",
        }),
      );
    });

    expect(await screen.findByText("Import Batch started for 2 dropped items.")).toBeInTheDocument();
    expect(client.startLibraryImport).toHaveBeenCalledTimes(1);
    expect(client.startLibraryImport).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174010");

    await act(async () => {
      fake.fire(
        dragSummary({
          phase: "drop",
          fileCount: 1,
          folderCount: 1,
          x: 200,
          y: 120,
          dropId: "123e4567-e89b-12d3-a456-426614174010",
        }),
      );
    });
    expect(client.startLibraryImport).toHaveBeenCalledTimes(1);
  });

  it("accepts drops only over the asset wall grid in a non-empty Library", async () => {
    const fake = new FakeNativeDrag();
    const { client } = renderWorkspace({ nativeDrag: fake.subscribe });

    const grid = await renderedWall("Assets");
    stubWallRect(grid, 100, 50, 500, 450);

    await act(async () => {
      fake.fire(dragSummary({ phase: "enter", fileCount: 2, x: 20, y: 30 }));
    });
    expect(screen.queryByText(/release to import/i)).not.toBeInTheDocument();

    await act(async () => {
      fake.fire(
        dragSummary({
          phase: "drop",
          fileCount: 2,
          x: 20,
          y: 30,
          dropId: "123e4567-e89b-12d3-a456-426614174011",
        }),
      );
    });

    expect(client.startLibraryImport).not.toHaveBeenCalled();
    expect(screen.queryByRole("status", { name: /release to import/i })).not.toBeInTheDocument();
  });

  it("ignores drops the host did not validate", async () => {
    const fake = new FakeNativeDrag();
    const { client } = renderWorkspace({ nativeDrag: fake.subscribe });

    const grid = await renderedWall("Assets");
    stubWallRect(grid, 100, 50, 500, 450);

    await act(async () => {
      fake.fire(
        dragSummary({
          phase: "drop",
          fileCount: 9,
          x: 150,
          y: 150,
          accepted: false,
          dropId: null,
        }),
      );
    });

    expect(client.startLibraryImport).not.toHaveBeenCalled();
  });

  it("surfaces conflict feedback when an Import Batch is already active or paused", async () => {
    const fake = new FakeNativeDrag();
    const client = createClient({
      startLibraryImport: vi.fn(async () => {
        throw conflictError;
      }),
    });
    renderWorkspace({ nativeDrag: fake.subscribe, client });

    const grid = await renderedWall("Assets");
    stubWallRect(grid, 100, 50, 500, 450);

    await act(async () => {
      fake.fire(
        dragSummary({
          phase: "drop",
          fileCount: 3,
          x: 150,
          y: 150,
          dropId: "123e4567-e89b-12d3-a456-426614174012",
        }),
      );
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An Import Batch is already running or paused.");
  });

  it("clears the release cue when the drag leaves the window", async () => {
    const fake = new FakeNativeDrag();
    renderWorkspace({ nativeDrag: fake.subscribe });

    const grid = await renderedWall("Assets");
    stubWallRect(grid, 100, 50, 500, 450);

    await act(async () => {
      fake.fire(dragSummary({ phase: "enter", fileCount: 1, x: 150, y: 150 }));
    });
    expect(screen.getByRole("status", { name: /release to import/i })).toBeInTheDocument();

    await act(async () => {
      fake.fire(dragSummary({ phase: "leave" }));
    });
    expect(screen.queryByRole("status", { name: /release to import/i })).not.toBeInTheDocument();
  });

  it("unsubscribes drag listeners on route change so responses never duplicate", async () => {
    const fake = new FakeNativeDrag();
    const first = renderWorkspace({ nativeDrag: fake.subscribe });
    expect(fake.activeCount).toBe(1);

    first.unmount();
    expect(fake.activeCount).toBe(0);

    const second = renderWorkspace({ nativeDrag: fake.subscribe });
    expect(fake.subscriptionCount).toBe(2);
    expect(fake.activeCount).toBe(1);
    second.unmount();
    expect(fake.activeCount).toBe(0);
  });
});
