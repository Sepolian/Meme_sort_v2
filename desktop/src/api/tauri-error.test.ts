import { describe, expect, it } from "vitest";
import { tauriErrorDetail } from "./tauri-error";

describe("tauriErrorDetail", () => {
  it("keeps the safe backend detail from a structured Tauri command error", () => {
    expect(tauriErrorDetail({ status: 404, error: "NotFound", detail: "Asset was not found.", retryable: false }, "Fallback"))
      .toBe("Asset was not found.");
  });
});
