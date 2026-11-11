import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { MemeSortClient } from "../../api/tauri-client";
import type { ImportTask } from "../../api/types";
import { ImportBatchContext, type ImportBatchStartResult } from "../import/ImportBatchContext";
import { LibraryImportMenu } from "./LibraryImportMenu";

function renderMenu(
  client: MemeSortClient = {} as MemeSortClient,
  startBatch: (start: () => Promise<ImportTask>) => Promise<ImportBatchStartResult> =
    async () => ({ kind: "blocked", reason: "start-in-progress" }),
) {
  render(
    <ImportBatchContext.Provider value={{
      snapshot: null,
      starting: false,
      startBatch,
      requestPause: async () => undefined,
      requestResume: async () => undefined,
      controlsPending: false,
    }}>
      <LibraryImportMenu client={client} />
    </ImportBatchContext.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
}

it("closes the Import menu with Escape and restores keyboard focus", () => {
  renderMenu();
  const trigger = screen.getByRole("button", { name: "Import" });
  screen.getByRole("menuitem", { name: "Choose Files" }).focus();
  fireEvent.keyDown(window, { key: "Escape" });

  expect(screen.queryByRole("menu", { name: "Import options" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("opens and navigates Import options with the keyboard", () => {
  renderMenu();
  const trigger = screen.getByRole("button", { name: "Import" });
  const files = screen.getByRole("menuitem", { name: "Choose Files" });
  const folder = screen.getByRole("menuitem", { name: "Choose Folder" });
  expect(files).toHaveFocus();
  fireEvent.keyDown(files, { key: "ArrowDown" });
  expect(folder).toHaveFocus();
  fireEvent.keyDown(folder, { key: "ArrowDown" });
  expect(files).toHaveFocus();
  fireEvent.keyDown(files, { key: "ArrowUp" });
  expect(folder).toHaveFocus();
  fireEvent.keyDown(folder, { key: "Home" });
  expect(files).toHaveFocus();
  fireEvent.keyDown(files, { key: "End" });
  expect(folder).toHaveFocus();

  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.keyDown(trigger, { key: "ArrowUp" });
  expect(screen.getByRole("menuitem", { name: "Choose Folder" })).toHaveFocus();
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(screen.getByRole("menuitem", { name: "Choose Files" })).toHaveFocus();
});

it("does not claim success when shared launch coordination blocks the import", async () => {
  const client = {
    chooseLibraryFiles: vi.fn(async () => ({ selection_id: "selection-1", count: 2 })),
    startLibraryImport: vi.fn(async () => { throw new Error("must not launch"); }),
  } as unknown as MemeSortClient;
  renderMenu(client);
  fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("already starting an Import Batch");
  expect(client.startLibraryImport).not.toHaveBeenCalled();
  expect(screen.queryByText(/Import Batch started for/)).not.toBeInTheDocument();
});

it("shows a backend import conflict when the local batch snapshot is stale", async () => {
  const client = {
    chooseLibraryFiles: vi.fn(async () => ({ selection_id: "selection-1", count: 2 })),
    startLibraryImport: vi.fn(async () => {
      throw { status: 409, error: "ImportBatchConflictError", detail: "An Import Batch is already running or paused." };
    }),
  } as unknown as MemeSortClient;
  renderMenu(client, async (start) => ({ kind: "started", snapshot: await start() }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("An Import Batch is already running or paused.");
});

it.each(["Files", "Folder"])("returns focus after cancelling Choose %s without starting an import", async (kind) => {
  const client = {
    chooseLibraryFiles: vi.fn(async () => null),
    chooseLibraryFolder: vi.fn(async () => null),
    startLibraryImport: vi.fn(),
  } as unknown as MemeSortClient;
  renderMenu(client);
  const choice = screen.getByRole("menuitem", { name: `Choose ${kind}` });
  choice.focus();
  fireEvent.click(choice);

  const trigger = screen.getByRole("button", { name: "Import" });
  await waitFor(() => expect(trigger).toBeEnabled());
  await waitFor(() => expect(trigger).toHaveFocus());
  expect(client.startLibraryImport).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("keeps an explicit focus move when a pending picker is cancelled", async () => {
  let cancelPicker!: (value: null) => void;
  const client = {
    chooseLibraryFiles: vi.fn(() => new Promise<null>((resolve) => { cancelPicker = resolve; })),
  } as unknown as MemeSortClient;
  renderMenu(client);
  const choice = screen.getByRole("menuitem", { name: "Choose Files" });
  choice.focus();
  fireEvent.click(choice);

  render(<button type="button">Other control</button>);
  const other = screen.getByRole("button", { name: "Other control" });
  other.focus();
  await act(async () => { cancelPicker(null); });
  await act(async () => {
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
  });
  expect(other).toHaveFocus();
});
