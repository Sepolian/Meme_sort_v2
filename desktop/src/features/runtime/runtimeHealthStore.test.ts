import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeHealthResult } from "../../api/types";
import {
  ensureAutomaticRuntimeHealthCheck,
  getRuntimeHealthSnapshot,
  resetRuntimeHealthForTesting,
  retryRuntimeHealthCheck,
} from "./runtimeHealthStore";

function healthyResult(): RuntimeHealthResult {
  return {
    runtime_fingerprint: "runtime-1",
    backend_name: "llama.cpp",
    device: "Vulkan0",
    gpu_name: "Test GPU",
    gpu_vendor: "amd",
    gpu_vendor_id: "0x1002",
    text_smoke_vector_dim: 2048,
    image_smoke_vector_dim: 2048,
    diagnostic_steps: [{ step: "image-embedding-smoke", status: "ok", detail: "Image embedding passed." }],
    smoke_test_ok: true,
    error: null,
  };
}

function failedResult(): RuntimeHealthResult {
  return { ...healthyResult(), smoke_test_ok: false, error: "Vulkan0 unavailable." };
}

describe("runtimeHealthStore", () => {
  beforeEach(() => {
    resetRuntimeHealthForTesting();
  });

  it("starts idle without a result", () => {
    expect(getRuntimeHealthSnapshot()).toMatchObject({ status: "idle", result: null, automaticStarted: false });
  });

  it("coalesces concurrent automatic starts into one client call", async () => {
    let resolve!: (value: RuntimeHealthResult) => void;
    const runRuntimeHealthCheck = vi.fn(() => new Promise<RuntimeHealthResult>((r) => (resolve = r)));
    const client = { runRuntimeHealthCheck } as never;

    const first = ensureAutomaticRuntimeHealthCheck(client);
    const second = ensureAutomaticRuntimeHealthCheck(client);
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);

    resolve(healthyResult());
    const [a, b] = await Promise.all([first, second]);
    expect(a.status).toBe("healthy");
    expect(b.status).toBe("healthy");
    expect(getRuntimeHealthSnapshot().status).toBe("healthy");
  });

  it("rejects a second automatic start after completion without another call", async () => {
    const runRuntimeHealthCheck = vi.fn(async () => healthyResult());
    const client = { runRuntimeHealthCheck } as never;

    await ensureAutomaticRuntimeHealthCheck(client);
    await ensureAutomaticRuntimeHealthCheck(client);
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
  });

  it("allows explicit Retry to start a later check after failure", async () => {
    const runRuntimeHealthCheck = vi.fn(async () => failedResult());
    const client = { runRuntimeHealthCheck } as never;

    await ensureAutomaticRuntimeHealthCheck(client);
    expect(getRuntimeHealthSnapshot().status).toBe("failed");

    runRuntimeHealthCheck.mockResolvedValueOnce(healthyResult());
    await retryRuntimeHealthCheck(client);
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(2);
    expect(getRuntimeHealthSnapshot().status).toBe("healthy");
  });

  it("shares the running result for concurrent Retry instead of racing", async () => {
    let resolve!: (value: RuntimeHealthResult) => void;
    const runRuntimeHealthCheck = vi.fn(() => new Promise<RuntimeHealthResult>((r) => (resolve = r)));
    const client = { runRuntimeHealthCheck } as never;

    const first = retryRuntimeHealthCheck(client);
    const second = retryRuntimeHealthCheck(client);
    expect(runRuntimeHealthCheck).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);

    resolve(healthyResult());
    await expect(first).resolves.toMatchObject({ status: "healthy" });
  });

  it("records thrown errors as failed with diagnostic detail", async () => {
    const runRuntimeHealthCheck = vi.fn(async () => {
      throw { error: "SidecarError", detail: "boom" };
    });
    const client = { runRuntimeHealthCheck } as never;

    await ensureAutomaticRuntimeHealthCheck(client);
    const snapshot = getRuntimeHealthSnapshot();
    expect(snapshot.status).toBe("failed");
    expect(snapshot.error).toContain("boom");
  });
});
