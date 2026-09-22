import { describe, expect, it } from "vitest";
import { tauriErrorCode, tauriErrorDetail } from "./tauri-error";

describe("tauriErrorDetail", () => {
  it("keeps the safe backend detail from a structured Tauri command error", () => {
    expect(tauriErrorDetail({ status: 404, error: "NotFound", detail: "Asset was not found.", retryable: false }, "Fallback"))
      .toBe("Asset was not found.");
  });

  it("preserves structured command error codes for recovery decisions", () => {
    expect(tauriErrorCode({ error: "ImageSelectionUnavailable" })).toBe("ImageSelectionUnavailable");
    expect(tauriErrorCode({ code: "TransientSidecarFailure" })).toBe("TransientSidecarFailure");
    expect(tauriErrorCode(new Error("not structured"))).toBeNull();
  });
});
