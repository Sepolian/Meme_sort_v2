import { describe, expect, it, vi } from "vitest";
import { createMemeSortClient } from "./tauri-client";

describe("createMemeSortClient", () => {
  it("invokes only the typed app-state command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ library_status: { total_assets: 0 } });
    const client = createMemeSortClient(invokeCommand);

    await client.getAppState();

    expect(invokeCommand).toHaveBeenCalledWith("get_app_state");
  });
});
