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
  copyAssetToClipboard: unsupported,
  copyOriginalFile: unsupported,
  copyOriginalFiles: unsupported,
  acceptDuplicatePair: unsupported,
  clearAcceptedPairs: unsupported,
};

async function unsupported(): Promise<never> {
  throw new Error("This test only renders the application shell.");
}

function renderApp(route = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <App client={client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("application shell navigation", () => {
  it("shows Library and Duplicates at the top with Settings anchored at the bottom", async () => {
    renderApp();
    await screen.findByRole("heading", { name: "Your library" });

    const primaryNav = screen.getByRole("navigation", { name: "Primary" });
    expect(primaryNav).toBeInTheDocument();
    expect(primaryNav).toHaveTextContent("Library");
    expect(primaryNav).toHaveTextContent("Duplicates");

    const settingsNav = screen.getByRole("navigation", { name: "Settings" });
    expect(settingsNav).toHaveTextContent("Settings");

    // Legacy routes leave the primary navigation but stay reachable directly.
    expect(screen.queryByRole("link", { name: "Setup" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Search" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Status" })).not.toBeInTheDocument();
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

  it("renders the Settings skeleton with all required sections", async () => {
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Appearance" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Accepted Duplicate Pairs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runtime" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Installation" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Advanced Diagnostics" })).toBeInTheDocument();
  });

  it("keeps the Library shell with toolbar, scrollable content, and inspector zones", async () => {
    const { container } = renderApp();
    await screen.findByRole("heading", { name: "Your library" });

    const shell = container.querySelector(".library-shell");
    expect(shell).not.toBeNull();
    expect(shell?.querySelector(".library-toolbar")).not.toBeNull();
    expect(shell?.querySelector(".library-content")).not.toBeNull();
    // Inspector region is optional until ticket 10, but the layout zone must exist in CSS/DOM contract.
    // The shell supports it via .library-body[data-inspector] without overlaying the content.
    expect(shell?.querySelector(".library-body")).not.toBeNull();
  });

  it.each([
    ["/setup", "Setup & runtime"],
    ["/search", "Search MemeSort"],
    ["/search/text", "Text search"],
    ["/search/image", "Image search"],
    ["/search/similar", "Find similar Assets"],
    ["/status", "Application status"],
  ])("keeps legacy route %s directly reachable", async (route, heading) => {
    renderApp(route);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("renders NotFoundPage for unknown routes", async () => {
    renderApp("/does-not-exist");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("switches the document theme without changing the active workspace", async () => {
    renderApp();
    await screen.findByRole("heading", { name: "Your library" });

    fireEvent.click(screen.getByRole("button", { name: "Use light theme" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(screen.getByRole("heading", { name: "Your library" })).toBeInTheDocument();
  });
});
