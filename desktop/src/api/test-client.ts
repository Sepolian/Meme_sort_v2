import { createMemeSortClient } from "./tauri-client";

export function createUnconfiguredClient() {
  return createMemeSortClient(async (command) => {
    throw new Error(`Unconfigured test command: ${command}`);
  });
}
