import { describe, expect, it, vi } from "vitest";
import { createMemeSortClient } from "./tauri-client";

describe("createMemeSortClient", () => {
  it("invokes only the typed app-state command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ library_status: { total_assets: 0 } });
    const client = createMemeSortClient(invokeCommand);

    await client.getAppState();

    expect(invokeCommand).toHaveBeenCalledWith("get_app_state");
  });

  it("observes the Import Batch through one fixed path-free status command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ batch_id: null, status: "idle", running: false });
    const client = createMemeSortClient(invokeCommand);

    await client.getImportStatus();
    await client.pauseImport();
    await client.resumeImport();

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "get_import_status");
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "pause_import");
    expect(invokeCommand).toHaveBeenNthCalledWith(3, "resume_import");
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

  it("uses path-free commands for a native-selected import", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ status: "running" });
    const client = createMemeSortClient(invokeCommand);

    await client.chooseImportFolder();
    await client.chooseSearchImage();
    await client.startImport();
    await client.startImportAndIndex();
    await client.pauseImport();
    await client.resumeImport();

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "choose_import_folder");
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "choose_search_image");
    expect(invokeCommand).toHaveBeenNthCalledWith(3, "start_import");
    expect(invokeCommand).toHaveBeenNthCalledWith(4, "start_import_and_index");
    expect(invokeCommand).toHaveBeenNthCalledWith(5, "pause_import");
    expect(invokeCommand).toHaveBeenNthCalledWith(6, "resume_import");
  });

  it("uses only ID and count commands for Library choosers", async () => {
    const invokeCommand = vi
      .fn()
      .mockResolvedValueOnce({ selection_id: "123e4567-e89b-12d3-a456-426614174010", count: 2 })
      .mockResolvedValueOnce({ selection_id: "123e4567-e89b-12d3-a456-426614174011", count: 1 })
      .mockResolvedValueOnce({ status: "running" });
    const client = createMemeSortClient(invokeCommand);

    const files = await client.chooseLibraryFiles();
    const folder = await client.chooseLibraryFolder();
    await client.startLibraryImport(files!.selection_id);

    expect(files).toEqual({ selection_id: "123e4567-e89b-12d3-a456-426614174010", count: 2 });
    expect(folder).toEqual({ selection_id: "123e4567-e89b-12d3-a456-426614174011", count: 1 });
    expect(invokeCommand).toHaveBeenNthCalledWith(1, "choose_library_files");
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "choose_library_folder");
    expect(invokeCommand).toHaveBeenNthCalledWith(3, "start_library_import", {
      selectionId: "123e4567-e89b-12d3-a456-426614174010",
    });
  });

  it("uses a UUID-scoped command pair for text Search Requests", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ results: [] });
    const client = createMemeSortClient(invokeCommand);
    const requestId = "123e4567-e89b-12d3-a456-426614174000";

    await client.searchText("surprised reaction", requestId);
    await client.cancelSearch(requestId);

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "search_text", { query: "surprised reaction", requestId });
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "cancel_search", { requestId });
  });

  it("uses a path-free command for a native-selected image Search Request", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ results: [] });
    const client = createMemeSortClient(invokeCommand);
    const requestId = "123e4567-e89b-12d3-a456-426614174000";

    await client.searchImage(requestId);

    expect(invokeCommand).toHaveBeenCalledWith("search_image", { requestId });
  });

  it("uses a fixed command for finding Assets similar to an Indexed Asset", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ results: [] });
    const client = createMemeSortClient(invokeCommand);
    const assetId = "123e4567-e89b-12d3-a456-426614174000";

    await client.findSimilar(assetId);

    expect(invokeCommand).toHaveBeenCalledWith("find_similar", { assetId });
  });

  it("uses a bounded threshold command for duplicate review", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ pairs: [] });
    const client = createMemeSortClient(invokeCommand);

    await client.getDuplicates(0.92);

    expect(invokeCommand).toHaveBeenCalledWith("get_duplicates", { threshold: 0.92 });
  });

  it("uses only named worker-loop, health-check, and failed-Job retry commands", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ running: true, paused: false });
    const client = createMemeSortClient(invokeCommand);

    await client.pauseWorkerLoop();
    await client.resumeWorkerLoop();
    await client.triggerWorkerLoop();
    await client.runRuntimeHealthCheck();
    await client.retryFailedJobs();

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "pause_worker_loop");
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "resume_worker_loop");
    expect(invokeCommand).toHaveBeenNthCalledWith(3, "trigger_worker_loop");
    expect(invokeCommand).toHaveBeenNthCalledWith(4, "run_runtime_health_check");
    expect(invokeCommand).toHaveBeenNthCalledWith(5, "retry_failed_jobs");
  });

  it("lists and removes only selected Pending Job records", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ jobs: [] });
    const client = createMemeSortClient(invokeCommand);
    const jobId = "123e4567-e89b-12d3-a456-426614174000";

    await client.getPendingJobs();
    await client.deletePendingJobs([jobId]);

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "get_pending_jobs");
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "delete_pending_jobs", { jobIds: [jobId] });
  });

  it("uses the fixed Asset reveal command for managed and recorded source files", async () => {
    const invokeCommand = vi.fn().mockResolvedValue(undefined);
    const client = createMemeSortClient(invokeCommand);
    const assetId = "123e4567-e89b-12d3-a456-426614174000";

    await client.revealAsset(assetId, "managed");
    await client.revealAsset(assetId, "source", "C:/Source/asset.gif");

    expect(invokeCommand).toHaveBeenNthCalledWith(1, "reveal_asset", { assetId, target: "managed" });
    expect(invokeCommand).toHaveBeenNthCalledWith(2, "reveal_asset", { assetId, target: "source", sourcePath: "C:/Source/asset.gif" });
  });

  it("opens the Library log directory without accepting a WebView path", async () => {
    const invokeCommand = vi.fn().mockResolvedValue(undefined);
    const client = createMemeSortClient(invokeCommand);

    await client.openLogDirectory();

    expect(invokeCommand).toHaveBeenCalledWith("open_log_directory");
  });

  it("copies one Asset to the clipboard through the fixed native command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue(undefined);
    const client = createMemeSortClient(invokeCommand);
    const assetId = "123e4567-e89b-12d3-a456-426614174000";

    await client.copyAssetToClipboard(assetId);

    expect(invokeCommand).toHaveBeenCalledWith("copy_asset_to_clipboard", { assetId });
  });

  it("copies one original Library Copy file reference through the fixed native command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue(undefined);
    const client = createMemeSortClient(invokeCommand);
    const assetId = "123e4567-e89b-12d3-a456-426614174000";

    await client.copyOriginalFile(assetId);

    expect(invokeCommand).toHaveBeenCalledWith("copy_original_file", { assetId });
  });

  it("copies many original Library Copy file references as one multi-file payload", async () => {
    const invokeCommand = vi.fn().mockResolvedValue(undefined);
    const client = createMemeSortClient(invokeCommand);
    const assetIds = [
      "123e4567-e89b-12d3-a456-426614174000",
      "123e4567-e89b-12d3-a456-426614174001",
    ];

    await client.copyOriginalFiles(assetIds);

    expect(invokeCommand).toHaveBeenCalledWith("copy_original_files", { assetIds });
  });

  it("persists one Accepted Duplicate Pair through the fixed bridge command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({
      library_root: "C:/Library",
      asset_a_id: "123e4567-e89b-12d3-a456-426614174000",
      asset_b_id: "123e4567-e89b-12d3-a456-426614174001",
      already_accepted: false,
    });
    const client = createMemeSortClient(invokeCommand);
    const assetAId = "123e4567-e89b-12d3-a456-426614174000";
    const assetBId = "123e4567-e89b-12d3-a456-426614174001";

    const result = await client.acceptDuplicatePair(assetAId, assetBId);

    expect(invokeCommand).toHaveBeenCalledWith("accept_duplicate_pair", { assetAId, assetBId });
    expect(result).toEqual({
      library_root: "C:/Library",
      asset_a_id: assetAId,
      asset_b_id: assetBId,
      already_accepted: false,
    });
  });

  it("clears Accepted Duplicate Pairs through the fixed bridge command", async () => {
    const invokeCommand = vi.fn().mockResolvedValue({ library_root: "C:/Library", cleared_pairs: 2 });
    const client = createMemeSortClient(invokeCommand);

    const result = await client.clearAcceptedPairs();

    expect(invokeCommand).toHaveBeenCalledWith("clear_accepted_pairs");
    expect(result).toEqual({ library_root: "C:/Library", cleared_pairs: 2 });
  });

  it("rejects clipboard and pair errors through the existing Tauri error path", async () => {
    const failure = { error: "SidecarError", detail: "Could not access the Asset Library Copy.", retryable: false };
    const invokeCommand = vi.fn().mockRejectedValue(failure);
    const client = createMemeSortClient(invokeCommand);

    await expect(
      client.copyAssetToClipboard("123e4567-e89b-12d3-a456-426614174000"),
    ).rejects.toEqual(failure);
    await expect(
      client.copyOriginalFile("123e4567-e89b-12d3-a456-426614174000"),
    ).rejects.toEqual(failure);
    await expect(
      client.copyOriginalFiles(["123e4567-e89b-12d3-a456-426614174000"]),
    ).rejects.toEqual(failure);
    await expect(
      client.acceptDuplicatePair(
        "123e4567-e89b-12d3-a456-426614174000",
        "123e4567-e89b-12d3-a456-426614174001",
      ),
    ).rejects.toEqual(failure);
    await expect(client.clearAcceptedPairs()).rejects.toEqual(failure);
  });
});
