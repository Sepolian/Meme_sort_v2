import { act, render, screen, waitFor } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { MemeSortClient } from "../../api/tauri-client";
import type { RuntimeHealthResult } from "../../api/types";
import { RuntimeHealthProvider } from "./RuntimeHealthProvider";
import { useRuntimeHealth } from "./useRuntimeHealth";
import type { RuntimeHealthSnapshot } from "./RuntimeHealthContext";

function result(smoke_test_ok: boolean): RuntimeHealthResult {
  return {
    runtime_fingerprint: "runtime-1",
    backend_name: "llama.cpp",
    device: "Vulkan0",
    gpu_name: "Test GPU",
    gpu_vendor: "amd",
    gpu_vendor_id: "0x1002",
    text_smoke_vector_dim: 2048,
    image_smoke_vector_dim: 2048,
    diagnostic_steps: [],
    smoke_test_ok,
    error: smoke_test_ok ? null : "Vulkan0 unavailable.",
  };
}

function renderHealth(
  queryClient: QueryClient,
  runRuntimeHealthCheck: () => Promise<RuntimeHealthResult>,
  capture?: (retry: () => Promise<RuntimeHealthSnapshot>) => void,
) {
  function Probe() {
    const health = useRuntimeHealth();
    capture?.(health.retry);
    return <output aria-label="Runtime health">{health.status}: {health.error}</output>;
  }
  const client = { runRuntimeHealthCheck } as MemeSortClient;
  return render(
    <QueryClientProvider client={queryClient}>
      <RuntimeHealthProvider client={client}><Probe /></RuntimeHealthProvider>
    </QueryClientProvider>,
  );
}

describe("runtime health query", () => {
  it("runs the local health check and Retry while offline", async () => {
    const queryClient = new QueryClient();
    const runRuntimeHealthCheck = vi.fn()
      .mockResolvedValueOnce(result(false))
      .mockResolvedValueOnce(result(true));
    let retry!: () => Promise<RuntimeHealthSnapshot>;
    const wasOnline = onlineManager.isOnline();
    onlineManager.setOnline(false);
    try {
      renderHealth(queryClient, runRuntimeHealthCheck, (next) => { retry = next; });
      await waitFor(() => expect(screen.getByRole("status", { name: "Runtime health" })).toHaveTextContent("failed: Vulkan0 unavailable."));
      await act(async () => { expect((await retry()).status).toBe("healthy"); });
      expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2);
    } finally {
      onlineManager.setOnline(wasOnline);
    }
  });

  it("does not rerun a failed automatic check on app remount", async () => {
    const queryClient = new QueryClient();
    const runRuntimeHealthCheck = vi.fn(async () => { throw { error: "SidecarError", detail: "boom" }; });
    const first = renderHealth(queryClient, runRuntimeHealthCheck);
    await waitFor(() => expect(screen.getByRole("status", { name: "Runtime health" })).toHaveTextContent("failed: boom"));
    first.unmount();

    renderHealth(queryClient, runRuntimeHealthCheck);
    expect(screen.getByRole("status", { name: "Runtime health" })).toHaveTextContent("failed: boom");
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
  });

  it("coalesces concurrent retries and accepts a later healthy result", async () => {
    const queryClient = new QueryClient();
    let resolve!: (value: RuntimeHealthResult) => void;
    const runRuntimeHealthCheck = vi.fn()
      .mockResolvedValueOnce(result(false))
      .mockImplementationOnce(() => new Promise<RuntimeHealthResult>((r) => { resolve = r; }));
    let retry!: () => Promise<RuntimeHealthSnapshot>;
    renderHealth(queryClient, runRuntimeHealthCheck, (next) => { retry = next; });
    await waitFor(() => expect(screen.getByRole("status", { name: "Runtime health" })).toHaveTextContent("failed: Vulkan0 unavailable."));

    await act(async () => {
      const first = retry();
      const second = retry();
      expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2);
      resolve(result(true));
      const outcomes = await Promise.all([first, second]);
      expect(outcomes.map((outcome) => outcome.status)).toEqual(["healthy", "healthy"]);
    });
    await waitFor(() => expect(screen.getByRole("status", { name: "Runtime health" })).toHaveTextContent("healthy:"));
  });
});
