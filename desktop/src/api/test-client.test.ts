import { expect, it } from "vitest";
import { createUnconfiguredClient } from "./test-client";

it("rejects unconfigured client commands", async () => {
  await expect(createUnconfiguredClient().getAssets()).rejects.toThrow("Unconfigured test command: get_assets");
});
