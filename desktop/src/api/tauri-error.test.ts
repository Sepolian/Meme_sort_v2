import { describe, expect, it } from "vitest";
import { isNativePickerCancellation, tauriErrorCode, tauriErrorDetail } from "./tauri-error";

describe("tauriErrorDetail", () => {
  it("keeps the safe backend detail from a structured Tauri command error", () => {
    expect(tauriErrorDetail({ status: 404, error: "NotFound", detail: "Asset was not found.", retryable: false }, "Fallback"))
      .toBe("Asset was not found.");
  });

  it("recognizes explicit native picker cancellation markers without swallowing other errors", () => {
    expect(isNativePickerCancellation({ cancelled: true })).toBe(true);
    expect(isNativePickerCancellation({ error: "DialogCancelled" })).toBe(true);
    expect(isNativePickerCancellation({ error: "PermissionDenied", detail: "Picker access was denied." })).toBe(false);
  });

  it("preserves structured command error codes for recovery decisions", () => {
    expect(tauriErrorCode({ error: "ImageSelectionUnavailable" })).toBe("ImageSelectionUnavailable");
    expect(tauriErrorCode({ code: "TransientSidecarFailure" })).toBe("TransientSidecarFailure");
    expect(tauriErrorCode(new Error("not structured"))).toBeNull();
  });
});
