import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ImportBatchProvider } from "./ImportBatchProvider";
import { LibraryImportMenu } from "../library/LibraryImportMenu";
import { AssetsWorkspace } from "../assets/AssetsWorkspace";
import type { MemeSortClient } from "../../api/tauri-client";
import type {
  NativeDragListener,
  NativeDragSubscribe,
  NativeDragSummary,
} from "../../api/native-drag";
import type { AssetListResult, ImportTask } from "../../api/types";
import { importSnapshot } from "./import-test-fixtures";

const SELECTION_ID = "123e4567-e89b-12d3-a456-426614174010";
const FOLDER_SELECTION_ID = "123e4567-e89b-12d3-a456-426614174011";

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

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolveFn, rejectFn) => {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise, resolve, reject };
}

class FakeNativeDrag {
  listeners = new Set<NativeDragListener>();

  subscribe: NativeDragSubscribe = (listener) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  fire(summary: NativeDragSummary) {
    [...this.listeners].forEach((listener) => listener(summary));
  }
}

function dropSummary(dropId: string): NativeDragSummary {
  return {
    phase: "drop",
    fileCount: 2,
    folderCount: 0,
    x: 150,
    y: 150,
    accepted: true,
    dropId,
  };
}

let currentStatus: ImportTask;
let startLibraryImport: ReturnType<typeof vi.fn>;

function createClient(): MemeSortClient {
  return {
    getImportStatus: vi.fn(async () => currentStatus),
    getAssets: vi.fn(async () => assets),
    chooseLibraryFiles: vi.fn(async () => ({ selection_id: SELECTION_ID, count: 2 })),
    chooseLibraryFolder: vi.fn(async () => ({ selection_id: FOLDER_SELECTION_ID, count: 1 })),
    startLibraryImport,
    pauseImport: vi.fn(async () => currentStatus),
    resumeImport: vi.fn(async () => currentStatus),
  } as unknown as MemeSortClient;
}

function renderStartSurface(client: MemeSortClient = createClient()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const drag = new FakeNativeDrag();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ImportBatchProvider client={client}>
        <LibraryImportMenu client={client} />
        <AssetsWorkspace
          client={client}
          selectedAssetId={null}
          onSelectAsset={() => undefined}
          onCloseDetail={() => undefined}
          nativeDrag={drag.subscribe}
        />
      </ImportBatchProvider>
    </QueryClientProvider>,
  );
  return { client, drag, invalidateSpy, queryClient, ...view };
}

async function stubWallRect() {
  const wall = await screen.findByLabelText("Assets");
  vi.spyOn(wall, "getBoundingClientRect").mockReturnValue({
    left: 100, top: 50, right: 500, bottom: 450,
    width: 400, height: 400, x: 100, y: 50,
    toJSON: () => undefined,
  } as DOMRect);
}

async function runMenuOption(name: "Choose Files" | "Choose Folder") {
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  fireEvent.click(await screen.findByRole("menuitem", { name }));
}

function appStateInvalidations(
  spy: { mock: { calls: Array<Array<unknown>> } },
): number {
  return spy.mock.calls.filter(([options]) => {
    const queryKey = (options as { queryKey?: readonly unknown[] } | undefined)?.queryKey;
    return Array.isArray(queryKey) && queryKey[0] === "app-state";
  }).length;
}

beforeEach(() => {
  currentStatus = importSnapshot();
  startLibraryImport = vi.fn(async () =>
    importSnapshot({ batch_id: "batch-1", status: "scanning", running: true, started_at: 1 }),
  );
});

describe("Import Batch launch coordination", () => {
  it("starts one batch when the menu and a native drop race inside one launch window", async () => {
    const pending = deferred<ImportTask>();
    startLibraryImport = vi.fn(async () => pending.promise);
    const { client, drag } = renderStartSurface();
    await stubWallRect();

    await act(async () => {
      await runMenuOption("Choose Files");
    });
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
    expect(startLibraryImport).toHaveBeenCalledWith(SELECTION_ID);

    await act(async () => {
      drag.fire(dropSummary("drop-1"));
    });
    // The blocked drop never reaches the backend and is never queued to run
    // later: only the menu's selection is launched.
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("already starting an Import Batch");

    await act(async () => {
      pending.resolve(
        importSnapshot({ batch_id: "batch-1", status: "scanning", running: true, started_at: 1 }),
      );
      await pending.promise;
    });

    expect(await screen.findByText("Import Batch started for 2 files.")).toBeInTheDocument();
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
    expect(client.getImportStatus).toHaveBeenCalled();
  });

  it("starts one batch when Choose Folder and a native drop race inside one launch window", async () => {
    const pending = deferred<ImportTask>();
    startLibraryImport = vi.fn(async () => pending.promise);
    const { drag } = renderStartSurface();
    await stubWallRect();

    await act(async () => {
      await runMenuOption("Choose Folder");
    });
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
    expect(startLibraryImport).toHaveBeenCalledWith(FOLDER_SELECTION_ID);

    await act(async () => {
      drag.fire(dropSummary("drop-folder"));
    });
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("already starting an Import Batch");

    await act(async () => {
      pending.resolve(
        importSnapshot({ batch_id: "batch-folder", status: "scanning", running: true, started_at: 1 }),
      );
      await pending.promise;
    });

    expect(await screen.findByText("Import Batch started for 1 folder.")).toBeInTheDocument();
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
  });

  it("refreshes app state once from the shared launch coordination", async () => {
    const { invalidateSpy } = renderStartSurface();
    await stubWallRect();

    await act(async () => {
      await runMenuOption("Choose Files");
    });
    await screen.findByText("Import Batch started for 2 files.");
    await waitFor(() => expect(appStateInvalidations(invalidateSpy)).toBe(1));
  });

  it("keeps a successful launch successful when the app-state refresh fails", async () => {
    const { invalidateSpy, queryClient } = renderStartSurface();
    invalidateSpy.mockImplementation((...args: unknown[]) => {
      const [options] = args as [{ queryKey?: readonly unknown[] } | undefined];
      if (Array.isArray(options?.queryKey) && options.queryKey[0] === "app-state") {
        return Promise.reject(new Error("app-state refresh failed"));
      }
      return (QueryClient.prototype.invalidateQueries as (...a: unknown[]) => Promise<void>).apply(
        queryClient,
        args,
      );
    });
    await stubWallRect();

    await act(async () => {
      await runMenuOption("Choose Files");
    });

    expect(await screen.findByText("Import Batch started for 2 files.")).toBeInTheDocument();
    expect(screen.queryByText(/could not start the Library Import Batch/i)).not.toBeInTheDocument();
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
  });

  it("blocks a valid native drop while a known batch is running, without calling the start command", async () => {
    currentStatus = importSnapshot({ batch_id: "batch-1", status: "importing", running: true });
    const { client, drag } = renderStartSurface();
    await stubWallRect();
    await waitFor(() => expect((client.getImportStatus as ReturnType<typeof vi.fn>).mock.calls.length)
      .toBeGreaterThanOrEqual(1));

    await act(async () => {
      drag.fire(dropSummary("drop-active"));
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("already running or paused");
    expect(startLibraryImport).not.toHaveBeenCalled();
  });

  it("still surfaces a backend conflict when the local snapshot is stale", async () => {
    startLibraryImport = vi.fn(async () => {
      throw conflictError;
    });
    const { drag } = renderStartSurface();
    await stubWallRect();

    await act(async () => {
      drag.fire(dropSummary("drop-conflict"));
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An Import Batch is already running or paused.",
    );
    expect(startLibraryImport).toHaveBeenCalledTimes(1);
    expect(startLibraryImport).toHaveBeenCalledWith("drop-conflict");
  });

  it("releases the launch state after a rejected start so a fresh native drop can start", async () => {
    startLibraryImport = vi
      .fn()
      .mockRejectedValueOnce({
        status: null,
        error: "ImportStartError",
        detail: "The sidecar refused this launch.",
        retryable: true,
      })
      .mockResolvedValue(
        importSnapshot({ batch_id: "batch-2", status: "scanning", running: true, started_at: 2 }),
      );
    const { drag } = renderStartSurface();
    await stubWallRect();

    await act(async () => {
      drag.fire(dropSummary("drop-first"));
    });
    expect(await screen.findByText("The sidecar refused this launch.")).toBeInTheDocument();

    await act(async () => {
      drag.fire(dropSummary("drop-second"));
    });

    expect(await screen.findByText("Import Batch started for 2 dropped items.")).toBeInTheDocument();
    expect(startLibraryImport).toHaveBeenCalledTimes(2);
    expect(startLibraryImport).toHaveBeenLastCalledWith("drop-second");
  });

  it("writes the launched snapshot immediately and keeps the Import entry disabled while launching", async () => {
    const pending = deferred<ImportTask>();
    startLibraryImport = vi.fn(async () => pending.promise);
    const { queryClient } = renderStartSurface();
    await stubWallRect();

    await act(async () => {
      await runMenuOption("Choose Files");
    });
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();

    const snapshot = importSnapshot({
      batch_id: "batch-9",
      status: "scanning",
      running: true,
      started_at: 9,
    });
    // The sidecar keeps reporting the launched batch, so a poll cannot
    // overwrite the snapshot the launch wrote.
    currentStatus = snapshot;
    await act(async () => {
      pending.resolve(snapshot);
      await pending.promise;
    });
    await screen.findByText("Import Batch started for 2 files.");
    expect(queryClient.getQueryData(["import-batch"])).toEqual(snapshot);
  });
});
