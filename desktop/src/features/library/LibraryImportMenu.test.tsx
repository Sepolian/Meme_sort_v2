import { fireEvent, render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { LibraryImportMenu } from "./LibraryImportMenu";
import type { MemeSortClient } from "../../api/tauri-client";
import { ImportBatchContext } from "../import/ImportBatchContext";
import type { ImportTask } from "../../api/types";
import { importSnapshot } from "../import/import-test-fixtures";

function renderMenu(options: {
  client?: MemeSortClient;
  snapshot?: ImportTask | null;
  startBatch?: (start: () => Promise<ImportTask>) => Promise<ImportTask>;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const startBatch =
    options.startBatch ??
    (async (start: () => Promise<ImportTask>) => {
      const snapshot = await start();
      queryClient.setQueryData(["import-batch"], snapshot);
      return snapshot;
    });
  const client =
    options.client ??
    ({
      chooseLibraryFiles: vi.fn(async () => ({
        selection_id: "123e4567-e89b-12d3-a456-426614174010",
        count: 2,
      })),
      chooseLibraryFolder: vi.fn(async () => ({
        selection_id: "123e4567-e89b-12d3-a456-426614174011",
        count: 1,
      })),
      startLibraryImport: vi.fn(async () => importSnapshot({ batch_id: "batch-1", status: "scanning", running: true, started_at: 1 })),
    } as unknown as MemeSortClient);
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ImportBatchContext.Provider
        value={{
          snapshot: options.snapshot ?? null,
          startBatch,
          requestPause: async () => undefined,
          requestResume: async () => undefined,
          controlsPending: false,
        }}
      >
        <LibraryImportMenu client={client} />
      </ImportBatchContext.Provider>
    </QueryClientProvider>,
  );
  return { client, invalidateSpy, ...view };
}

async function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  expect(await screen.findByRole("menu", { name: "Import options" })).toBeInTheDocument();
}

describe("LibraryImportMenu", () => {
  it("exposes one compact Import menu with Choose Files and Choose Folder", async () => {
    renderMenu();
    expect(screen.getByRole("button", { name: "Import" })).toHaveAttribute("aria-haspopup", "menu");
    await openMenu();
    expect(screen.getByRole("menuitem", { name: "Choose Files" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Choose Folder" })).toBeInTheDocument();
  });

  it("starts a file import through the exact picker and only the selection ID", async () => {
    const { client } = renderMenu();
    await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));

    expect(await screen.findByText("Import Batch started for 2 files.")).toBeInTheDocument();
    expect(client.chooseLibraryFiles).toHaveBeenCalledTimes(1);
    expect(client.startLibraryImport).toHaveBeenCalledTimes(1);
    expect(client.startLibraryImport).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174010");
  });

  it("starts a folder import through the exact picker and only the selection ID", async () => {
    const { client } = renderMenu();
    await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Folder" }));

    expect(await screen.findByText("Import Batch started for 1 folder.")).toBeInTheDocument();
    expect(client.chooseLibraryFolder).toHaveBeenCalledTimes(1);
    expect(client.startLibraryImport).toHaveBeenCalledWith("123e4567-e89b-12d3-a456-426614174011");
  });

  it("treats picker cancellation as a no-op without mutation or error toast", async () => {
    const client = {
      chooseLibraryFiles: vi.fn(async () => null),
      chooseLibraryFolder: vi.fn(async () => null),
      startLibraryImport: vi.fn(async () => {
        throw new Error("must not start after cancellation");
      }),
    } as unknown as MemeSortClient;
    renderMenu({ client });
    await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(client.startLibraryImport).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("disables the Import entry while a batch is running or paused", async () => {
    const running = renderMenu({
      snapshot: importSnapshot({ batch_id: "batch-1", status: "importing", running: true }),
    });
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
    running.unmount();

    renderMenu({
      snapshot: importSnapshot({
        batch_id: "batch-1",
        status: "paused",
        running: true,
        paused: true,
        pause_requested: true,
      }),
    });
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("surfaces active-batch conflicts without exposing raw paths", async () => {
    const client = {
      chooseLibraryFiles: vi.fn(async () => ({
        selection_id: "123e4567-e89b-12d3-a456-426614174010",
        count: 2,
      })),
      chooseLibraryFolder: vi.fn(async () => null),
      startLibraryImport: vi.fn(async () => {
        throw {
          status: 409,
          error: "ImportBatchConflictError",
          detail: "An Import Batch is already running or paused.",
          retryable: false,
        };
      }),
    } as unknown as MemeSortClient;
    renderMenu({ client });
    await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An Import Batch is already running or paused.");
  });

  it("invalidates app state after starting an import (task polling picks up the batch)", async () => {
    const { invalidateSpy } = renderMenu();
    await openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));

    await screen.findByText("Import Batch started for 2 files.");
    const appStateCalls = invalidateSpy.mock.calls.filter(([options]) => {
      const key = (options as { queryKey?: readonly unknown[] } | undefined)?.queryKey;
      return Array.isArray(key) && key[0] === "app-state";
    });
    expect(appStateCalls.length).toBeGreaterThanOrEqual(1);
  });

  it("closes the menu with Escape for keyboard users", async () => {
    renderMenu();
    await openMenu();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "Import options" })).not.toBeInTheDocument();
  });
});
