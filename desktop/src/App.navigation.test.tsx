import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import type { MemeSortClient } from "./api/tauri-client";
import { importSnapshot } from "./features/import/import-test-fixtures";

const client: MemeSortClient = {
  getAppState: async () => ({
    library_root: "C:/Library",
    runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
    setup_state: { health_check_ok: true },
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
  chooseImportFolder: unsupported,
  chooseSearchImage: unsupported,
  chooseLibraryFiles: unsupported,
  chooseLibraryFolder: unsupported,
  startLibraryImport: unsupported,
  startImport: unsupported,
  startImportAndIndex: unsupported,
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
  getPendingJobs: unsupported,
  deletePendingJobs: unsupported,
  cancelSearch: unsupported,
};

async function unsupported(): Promise<never> {
  throw new Error("This test only renders the application shell.");
}

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("application shell navigation", () => {
  it("navigates between the primary library, setup, and status workspaces", async () => {
    renderApp();
    await screen.findByRole("heading", { name: "Your library" });

    fireEvent.click(screen.getByRole("link", { name: "Setup" }));
    expect(await screen.findByRole("heading", { name: "Setup & runtime" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Status" }));
    expect(await screen.findByRole("heading", { name: "Application status" })).toBeInTheDocument();
  });

  it("switches the document theme without changing the active workspace", async () => {
    renderApp();
    await screen.findByRole("heading", { name: "Your library" });

    fireEvent.click(screen.getByRole("button", { name: "Use light theme" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(screen.getByRole("heading", { name: "Your library" })).toBeInTheDocument();
  });
});
