import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Component, type ReactNode } from "react";
import { App } from "./App";
import type { MemeSortClient } from "./api/tauri-client";
import { scheduleFocusRestoration } from "./components/useEscapeSurface";
import { importSnapshot } from "./features/import/import-test-fixtures";

const client: MemeSortClient = {
  getAppState: async () => ({
    library_root: "C:/Library",
    runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
    library_status: { total_assets: 1, job_counts: { pending: 0 } },
    worker_loop: { paused: false, running: true },
    import_task: importSnapshot(),
    pending_jobs: [],
  }),
  getImportStatus: async () => importSnapshot(),
  getAssets: unsupported,
  getAssetDetail: unsupported,
  revealAsset: unsupported,
  openLogDirectory: unsupported,
  deleteAsset: unsupported,
  removeSourceRecord: unsupported,
  batchAssetAction: unsupported,
  chooseSearchImage: unsupported,
  chooseLibraryFiles: unsupported,
  chooseLibraryFolder: unsupported,
  startLibraryImport: unsupported,
  pauseImport: unsupported,
  resumeImport: unsupported,
  searchText: unsupported,
  searchImage: unsupported,
  findSimilar: unsupported,
  getDuplicates: unsupported,
  pauseWorkerLoop: unsupported,
  resumeWorkerLoop: unsupported,
  triggerWorkerLoop: unsupported,
  runRuntimeHealthCheck: unsupported,
  retryFailedJobs: unsupported,
  deletePendingJobs: unsupported,
  cancelSearch: unsupported,
  copyAssetToClipboard: unsupported,
  copyOriginalFile: unsupported,
  copyOriginalFiles: unsupported,
  acceptDuplicatePair: unsupported,
  clearAcceptedPairs: unsupported,
};

async function unsupported(): Promise<never> {
  throw new Error("This test only renders the application shell.");
}

class RenderBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function AbandonRender(): never {
  throw new Error("abandon this render");
}

function renderApp(route = "/", testClient: MemeSortClient = client) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={testClient} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("application shell navigation", () => {
  beforeEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  it("navigates between the final Library, Duplicates, and Settings surfaces", async () => {
    renderApp();
    await screen.findByRole("heading", { name: "Your library" });

    fireEvent.click(screen.getByRole("link", { name: "Duplicates" }));
    expect(await screen.findByRole("heading", { name: "Duplicate assets" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Library" }));
    expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();
  });

  it("passes only native file and folder selection IDs to Import Batch", async () => {
    const filesClient = {
      ...client,
      chooseLibraryFiles: vi.fn(async () => ({ selection_id: "selection-files", count: 2 })),
      startLibraryImport: vi.fn(async () => importSnapshot({ batch_id: "batch-files", status: "scanning", running: true, started_at: 1 })),
    };
    const files = renderApp("/", filesClient);
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));
    expect(await screen.findByText(/Import Batch started for 2 files/)).toBeInTheDocument();
    expect(filesClient.chooseLibraryFiles).toHaveBeenCalledTimes(1);
    expect(filesClient.startLibraryImport).toHaveBeenCalledWith("selection-files");
    files.unmount();

    const folderClient = {
      ...client,
      chooseLibraryFolder: vi.fn(async () => ({ selection_id: "selection-folder", count: 1 })),
      startLibraryImport: vi.fn(async () => importSnapshot({ batch_id: "batch-folder", status: "scanning", running: true, started_at: 1 })),
    };
    renderApp("/", folderClient);
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Choose Folder" }));
    expect(await screen.findByText(/Import Batch started for 1 folder/)).toBeInTheDocument();
    expect(folderClient.chooseLibraryFolder).toHaveBeenCalledTimes(1);
    expect(folderClient.startLibraryImport).toHaveBeenCalledWith("selection-folder");
  });

  it("leaves Import Batch untouched when the native picker is cancelled", async () => {
    const cancelled = {
      ...client,
      chooseLibraryFiles: vi.fn(async () => null),
      startLibraryImport: vi.fn(async () => { throw new Error("must not start after cancellation"); }),
    };
    renderApp("/", cancelled);
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "Choose Files" }));
    });
    expect(cancelled.chooseLibraryFiles).toHaveBeenCalledTimes(1);
    expect(cancelled.startLibraryImport).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert", { name: "Import Batch result" })).not.toBeInTheDocument();
  });

  it("closes keyboard help with Escape and restores focus", async () => {
    renderApp("/settings");
    await screen.findByRole("heading", { name: "Settings" });

    const trigger = screen.getByRole("button", { name: "Keyboard help" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "MemeSort navigation" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "MemeSort navigation" })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });
  });

  it("does not invalidate committed focus restoration from an abandoned App render", () => {
    const cancelFrame = vi.spyOn(window, "cancelAnimationFrame");
    const cancelPendingRestoration = scheduleFocusRestoration(() => undefined);
    cancelFrame.mockClear();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <RenderBoundary>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/"]}>
            <App client={client} />
            <AbandonRender />
          </MemoryRouter>
        </QueryClientProvider>
      </RenderBoundary>,
    );

    expect(cancelFrame).not.toHaveBeenCalled();
    cancelPendingRestoration();
    cancelFrame.mockRestore();
    consoleError.mockRestore();
  });

  it("renders NotFoundPage for unknown routes", async () => {
    renderApp("/does-not-exist");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

});
