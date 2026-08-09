import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
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
  it("shows state returned through the typed Tauri client", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <App client={client} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "MemeSort is connected" })).toBeInTheDocument();
    expect(screen.getByText("llama.cpp / Vulkan0")).toBeInTheDocument();
    expect(screen.getByText("2 pending job(s)")).toBeInTheDocument();
  });
});
