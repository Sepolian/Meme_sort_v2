import { describe, expect, it, vi } from "vitest";
import { createMemeSortClient } from "./tauri-client";

describe("createMemeSortClient", () => {
  it("invokes only the typed app-state command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ library_status: { total_assets: 0 } });
    const client = createMemeSortClient(invokeCommand);

    await client.getAppState();

    expect(invokeCommand).toHaveBeenCalledWith("get_app_state");
  });

  it("invokes the fixed asset commands", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ assets: [] });
    const client = createMemeSortClient(invokeCommand);

    await client.getAssets();
    await client.getAssetDetail("123e4567-e89b-12d3-a456-426614174000");

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "get_assets");
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "get_asset_detail", {
      assetId: "123e4567-e89b-12d3-a456-426614174000",
    });
  });

  it("invokes only named Asset mutation commands", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ affected_asset_ids: [] });
    const client = createMemeSortClient(invokeCommand);

    await client.deleteAsset("123e4567-e89b-12d3-a456-426614174000");
    await client.removeSourceRecord("123e4567-e89b-12d3-a456-426614174000", "C:/Source/asset.gif");
    await client.batchAssetAction("rebuild-active-index", ["123e4567-e89b-12d3-a456-426614174000"]);

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "delete_asset", { assetId: "123e4567-e89b-12d3-a456-426614174000" });
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "remove_source_record", { assetId: "123e4567-e89b-12d3-a456-426614174000", sourcePath: "C:/Source/asset.gif" });
    expect(invokeCommand).toHaveBeenNthCalledWith(3, "batch_asset_action", {
      action: "rebuild-active-index",
      assetIds: ["123e4567-e89b-12d3-a456-426614174000"],
    });
  });
});
