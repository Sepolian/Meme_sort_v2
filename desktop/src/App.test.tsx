import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";

const client = {
  getAppState: async () => ({
    library_root: "C:/Library",
    runtime: { backend_name: "llama.cpp", device: "Vulkan0" },
    setup_state: { health_check_ok: false },
    library_status: { total_assets: 3, job_counts: { pending: 2 } },
    worker_loop: { paused: true, running: true },
    pending_jobs: [{ job_id: "job-1" }],
  }),
};

describe("App", () => {
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

  it("shows state returned through the typed Tauri client", async () => {
    renderApp();

    expect(await screen.findByRole("heading", { name: "Your library" })).toBeInTheDocument();
    expect(screen.getByText("llama.cpp / Vulkan0")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it.each([
    ["/", "Your library"],
    ["/setup", "Setup & runtime"],
    ["/search", "Search MemeSort"],
    ["/search/text", "Text search"],
    ["/search/image", "Image search"],
    ["/search/similar", "Find similar Assets"],
    ["/duplicates", "Duplicate assets"],
    ["/status", "Application status"],
  ])("renders the %s route when it is opened directly", async (route, heading) => {
    renderApp(route);

    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("shows a runtime-not-ready state on setup", async () => {
    renderApp("/setup");

    expect(await screen.findByText("Runtime not ready")).toBeInTheDocument();
  });

  it("closes the keyboard help dialog with Escape", async () => {
    renderApp("/status");
    await screen.findByRole("heading", { name: "Application status" });

    fireEvent.click(screen.getByRole("button", { name: "Keyboard help" }));
    expect(screen.getByRole("dialog", { name: "MemeSort navigation" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "MemeSort navigation" })).not.toBeInTheDocument();
  });
});
