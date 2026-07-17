const state = {
  runtimeProfiles: [],
  modelVariants: [],
  runtimeSettings: null,
  setupState: null,
  assetSummary: null,
  libraryStatus: null,
  workerLoop: null,
  importTask: null,
  importPollTimer: null,
  pendingJobs: [],
  selectedPendingJobIds: new Set(),
  selectedAsset: null,
  selectedAssetIds: new Set(),
  duplicatePairs: [],
  lastHealthDiagnosticSteps: [],
  activeSearchRequestIds: new Set(),
};

const LIBRARY_DETAIL_WIDTH_KEY = "memesort.libraryDetailWidth";
const THEME_KEY = "memesort.theme";
const MOBILE_STACK_BREAKPOINT = 1180;

const pageCopy = {
  libraryTab: {
    title: "All assets",
    subtitle: "Local memes, GIFs, semantic retrieval, and duplicate review from the active index recipe.",
  },
  setupTab: {
    title: "Import & runtime",
    subtitle: "Prepare the embedding runtime, import local folders, and start the index worker.",
  },
  searchTab: {
    title: "Semantic search",
    subtitle: "Search indexed assets with text, a local image, or an existing library asset.",
  },
  duplicatesTab: {
    title: "Duplicates",
    subtitle: "Compare candidate duplicate assets using the active recipe only.",
  },
  statusTab: {
    title: "Queue & status",
    subtitle: "Inspect asset state, queued work, recent jobs, and worker loop events.",
  },
};

function byId(id) {
  return document.getElementById(id);
}

function clampNumber(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || "Request failed");
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function looksLikeLocalModelPath(value) {
  const text = String(value || "").trim();
  if (!text) {
    return false;
  }
  return /^[A-Za-z]:[\\/]/.test(text) || text.startsWith("\\\\") || text.startsWith("/") || text.startsWith(".");
}

function setText(id, value) {
  const node = byId(id);
  if (node) {
    node.textContent = value ?? "";
  }
}

function setCssWidthVariable(variableName, width) {
  document.documentElement.style.setProperty(variableName, `${Math.round(width)}px`);
}

function isStackedSidebarLayout() {
  return window.matchMedia(`(max-width: ${MOBILE_STACK_BREAKPOINT}px)`).matches;
}

function switchTab(tabId) {
  if (tabId !== "searchTab") {
    cancelActiveSearches();
  }
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === tabId);
  });
  document.querySelectorAll(".tab-link").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabId);
  });
  const copy = pageCopy[tabId] || pageCopy.libraryTab;
  setText("pageTitle", copy.title);
  setText("pageSubtitle", copy.subtitle);
  setText("breadcrumbLabel", copy.title);
}

function applyTheme(theme) {
  const resolvedTheme = theme === "light" ? "light" : "dark";
  document.body.dataset.theme = resolvedTheme;
  const button = byId("themeToggle");
  if (button) {
    button.textContent = resolvedTheme === "dark" ? "Light theme" : "Dark theme";
  }
  try {
    window.localStorage.setItem(THEME_KEY, resolvedTheme);
  } catch {
    // Theme still applies for this session if storage is unavailable.
  }
}

function initializeTheme() {
  let storedTheme = "dark";
  try {
    storedTheme = window.localStorage.getItem(THEME_KEY) || storedTheme;
  } catch {
    // Keep the deliberate dark default when storage is unavailable.
  }
  applyTheme(storedTheme);
}

function formatDate(value) {
  if (!value) {
    return "n/a";
  }
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
}

function summarizeProfile(profile) {
  if (!profile) {
    return "";
  }
  return `${profile.label} | ${profile.device} | still <= ${profile.still_max_side}px | gif <= ${profile.gif_max_side}px | gif frames ${profile.gif_frame_count}`;
}

function selectedProfile() {
  const profileId = byId("profileSelect").value;
  return state.runtimeProfiles.find((profile) => profile.profile_id === profileId) || null;
}

function selectedModelVariant() {
  const modelKey = byId("modelVariantSelect").value;
  return state.modelVariants.find((variant) => variant.model_key === modelKey) || null;
}

function syncSetupSelections(force = false) {
  const profile = selectedProfile();
  if (!profile) {
    return;
  }
  const supportedModels = profile.supported_model_keys || state.modelVariants.map((item) => item.model_key);
  [...byId("modelVariantSelect").options].forEach((option) => {
    option.disabled = !supportedModels.includes(option.value);
  });
  if (!supportedModels.includes(byId("modelVariantSelect").value)) {
    byId("modelVariantSelect").value = supportedModels[0] || "";
    byId("modelPathInput").value = "";
  }
  const modelVariant = selectedModelVariant();
  const frameInput = byId("gifFrameCountInput");
  if (force || Number(frameInput.value) !== Number(profile.gif_frame_count)) {
    frameInput.value = String(profile.gif_frame_count);
  }
  renderProfileSummary();
  renderModelVariantSummary();
  const input = byId("modelPathInput");
  if (force && modelVariant) {
    input.placeholder = selectedBackendName() === "llama.cpp"
      ? "Local folder with main Q4_K_M GGUF + mmproj GGUF"
      : modelVariant.model_id;
    const configuredPath = state.runtimeSettings?.model_name_or_path || "";
    const recommendedSource = state.setupState?.runtime_readiness?.recommended_model_source || "";
    if (!configuredPath && recommendedSource) {
      input.value = recommendedSource;
    }
  }
  renderModelPathHint();
}

function renderProfiles() {
  const select = byId("profileSelect");
  const previous = select.value;
  select.innerHTML = "";

  state.runtimeProfiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.profile_id;
    option.textContent = profile.label;
    if (
      (state.runtimeSettings && state.runtimeSettings.selected_profile === profile.profile_id) ||
      (!state.runtimeSettings && previous === profile.profile_id)
    ) {
      option.selected = true;
    }
    select.appendChild(option);
  });

  if (!select.value && state.runtimeProfiles[0]) {
    select.value = state.runtimeProfiles[0].profile_id;
  }

  syncSetupSelections(true);
}

function renderModelVariants() {
  const select = byId("modelVariantSelect");
  const previous = select.value;
  select.innerHTML = "";

  state.modelVariants.forEach((variant) => {
    const option = document.createElement("option");
    option.value = variant.model_key;
    option.textContent = variant.label;
    if (
      (state.runtimeSettings && state.runtimeSettings.selected_model_key === variant.model_key) ||
      (!state.runtimeSettings && previous === variant.model_key)
    ) {
      option.selected = true;
    }
    select.appendChild(option);
  });

  if (!select.value && state.modelVariants[0]) {
    select.value = state.modelVariants[0].model_key;
  }

  syncSetupSelections(true);
}

function renderProfileSummary() {
  const profile = selectedProfile();
  const node = byId("profileSummary");
  if (!profile) {
    node.textContent = "";
    return;
  }
  node.innerHTML = `
    <div class="small"><strong>${escapeHtml(profile.label)}</strong></div>
    <div class="small muted">${escapeHtml(profile.notes || "")}</div>
    <div class="small mono">${escapeHtml(summarizeProfile(profile))}</div>
    <div class="small muted">Default recipe: ${escapeHtml(profile.recipe_preset)} | ${escapeHtml(profile.backend_name || "qwen3-vl")} | ${escapeHtml(profile.torch_dtype)}</div>
  `;
}

async function cancelActiveSearches() {
  const requestIds = [...state.activeSearchRequestIds];
  state.activeSearchRequestIds.clear();
  await Promise.allSettled(
    requestIds.map((requestId) =>
      api("/api/search/cancel", {
        method: "POST",
        body: JSON.stringify({ request_id: requestId }),
      })
    )
  );
}

function selectedBackendName() {
  return selectedProfile()?.backend_name || "qwen3-vl";
}

function renderModelVariantSummary() {
  const variant = selectedModelVariant();
  const node = byId("modelVariantSummary");
  if (!variant) {
    node.textContent = "";
    return;
  }
  node.innerHTML = `
    <div class="small"><strong>${escapeHtml(variant.label)}</strong></div>
    <div class="small muted">${escapeHtml(variant.notes || "")}</div>
    <div class="small mono">${escapeHtml(variant.model_id)} | ${escapeHtml(variant.output_dimension)}d</div>
  `;
}

function renderRuntimeSummary() {
  if (!state.runtimeSettings) {
    return;
  }

  setText(
    "runtimeSummary",
    [
      state.runtimeSettings.selected_profile,
      state.runtimeSettings.selected_model_key,
      state.runtimeSettings.selected_recipe_preset,
      state.runtimeSettings.backend_name || "qwen3-vl",
      `gif-f${state.runtimeSettings.gif_frame_count}`,
    ].join(" / ")
  );
  setText("activeRecipe", state.assetSummary?.active_recipe_label || "No active index yet");

  const suggestedModelPath = state.setupState?.suggested_model_path || "";
  byId("modelPathInput").value = "";
  byId("gifFrameCountInput").value = String(state.runtimeSettings.gif_frame_count);
  if (state.runtimeSettings.selected_profile) {
    byId("profileSelect").value = state.runtimeSettings.selected_profile;
  }
  if (state.runtimeSettings.selected_model_key) {
    byId("modelVariantSelect").value = state.runtimeSettings.selected_model_key;
  }
  renderProfileSummary();
  renderModelVariantSummary();
  renderModelPathHint();
}

function renderModelPathHint() {
  const node = byId("modelPathHint");
  const configuredPath = byId("modelPathInput").value.trim();
  const suggestedPath = state.setupState?.suggested_model_path || "";
  const recommendedSource = state.setupState?.runtime_readiness?.recommended_model_source || "";
  const variant = selectedModelVariant();
  const llamaCpp = selectedBackendName() === "llama.cpp";

  if (llamaCpp) {
    node.innerHTML = configuredPath
      ? `
        <div class="small"><strong>Configured GGUF bundle</strong></div>
        <div class="small muted">This must be a local folder containing one Q4_K_M main GGUF and one mmproj*.gguf, or the exact main GGUF file.</div>
        <div class="small mono">${escapeHtml(configuredPath)}</div>
      `
      : `
        <div class="small"><strong>Local GGUF bundle required</strong></div>
        <div class="small muted">GGUF auto-download is intentionally disabled in this first version. Choose a prepared Q4_K_M model folder; MemeSort also checks .models/gguf/${escapeHtml(variant?.model_key || "qwen3-2b")}-q4_k_m.</div>
      `;
    return;
  }

  if (configuredPath) {
    const configuredIsRepoId = !looksLikeLocalModelPath(configuredPath);
    const usingSuggested = configuredIsRepoId && suggestedPath && recommendedSource && recommendedSource === suggestedPath;
    node.innerHTML = `
      <div class="small"><strong>Configured model source</strong></div>
      <div class="small muted">${usingSuggested ? "Configured with a repo id; runtime will use the local project snapshot that matches it." : "Using the configured override path or repo id."}</div>
      <div class="small mono">${escapeHtml(configuredPath)}</div>
      ${usingSuggested ? `<div class="small mono">${escapeHtml(recommendedSource)}</div>` : ""}
    `;
    return;
  }

  if (suggestedPath) {
    node.innerHTML = `
      <div class="small"><strong>Local model ready</strong></div>
      <div class="small muted">A local snapshot was found for this model variant. Health check and indexing will use it automatically.</div>
      <div class="small mono">${escapeHtml(suggestedPath)}</div>
    `;
    return;
  }

  node.innerHTML = `
    <div class="small"><strong>Model will be prepared on demand</strong></div>
    <div class="small muted">Leave this blank to auto-download the selected model into the project-local .models folder during health check.</div>
    <div class="small mono">${escapeHtml(variant?.model_id || "Qwen/Qwen3-VL-Embedding-2B")}</div>
  `;
}

function renderRuntimeReadiness() {
  const node = byId("runtimeReadiness");
  const readiness = state.setupState?.runtime_readiness || null;
  if (!readiness) {
    node.textContent = "";
    return;
  }

  node.innerHTML = `
    <div class="small"><strong>Runtime readiness</strong></div>
    <div class="small muted">${escapeHtml(readiness.ready_detail || readiness.last_health_check_summary || "Health check has not been run yet.")}</div>
    <div class="small mono">${escapeHtml(readiness.selected_profile || "")} / ${escapeHtml(readiness.selected_model_label || readiness.selected_model_key || "")} / ${escapeHtml(readiness.backend_name || "")}</div>
    <div class="small mono">${escapeHtml(readiness.ready ? "Ready for indexing" : "Not ready for indexing")}</div>
    <div class="small mono">${escapeHtml(readiness.recommended_model_source || "No local model source discovered yet.")}</div>
  `;
}

function renderSetupGuide() {
  const node = byId("setupGuide");
  const setupState = state.setupState || null;
  if (!setupState) {
    node.textContent = "";
    return;
  }

  let title = "First-run guide";
  let detail = "Choose a runtime profile and embedding model to begin.";

  if (!setupState.runtime_profile_selected || !setupState.embedding_model_selected) {
    detail = "Pick the runtime profile and embedding model that should own the active index recipe.";
  } else if (!setupState.health_check_has_run) {
    detail = "Run health check next. This will verify the runtime and auto-download the selected model into the project-local .models folder if needed.";
  } else if (!setupState.health_check_ok) {
    detail = "Fix the runtime issue shown below, then rerun health check before importing or indexing.";
  } else if (!setupState.assets_present) {
    detail = "The runtime is ready. Import your first folder into the managed library.";
  } else if (!setupState.indexed_assets_present) {
    detail = "Assets are present but not searchable yet. Start indexing or resume the worker loop.";
  } else {
    title = "Ready";
    detail = "The first library is indexed. You can search, review duplicates, and continue importing.";
  }

  node.innerHTML = `
    <div class="small"><strong>${escapeHtml(title)}</strong></div>
    <div class="small muted">${escapeHtml(detail)}</div>
  `;
}

function renderSetupChecklist() {
  const container = byId("setupChecklist");
  const checklist = state.setupState?.checklist || [];
  container.innerHTML = "";
  if (!checklist.length) {
    container.innerHTML = `<div class="detail-empty">Setup progress is not available yet.</div>`;
    return;
  }
  checklist.forEach((item) => {
    const node = document.createElement("div");
    node.className = `checklist-item${item.done ? " done" : ""}`;
    node.innerHTML = `
      <div><strong>${item.done ? "Done" : "Next"}</strong> ${escapeHtml(item.label)}</div>
      <div class="small muted">${escapeHtml(item.detail || "")}</div>
    `;
    container.appendChild(node);
  });
}

function renderHealthDiagnostics() {
  const container = byId("healthDiagnostics");
  const diagnosticSteps = state.lastHealthDiagnosticSteps || [];

  container.innerHTML = "";
  if (!diagnosticSteps.length) {
    container.innerHTML = `<div class="detail-empty">Run health check to see step-by-step runtime diagnostics.</div>`;
    return;
  }

  diagnosticSteps.forEach((step) => {
    const node = document.createElement("div");
    const status = step.status === "ok" ? "done" : "";
    node.className = `checklist-item ${status}`.trim();
    node.innerHTML = `
      <div><strong>${escapeHtml(step.status === "ok" ? "OK" : "Issue")}</strong> ${escapeHtml(step.step || "step")}</div>
      <div class="small muted">${escapeHtml(step.detail || "")}</div>
    `;
    container.appendChild(node);
  });
}

function assetPreviewUrl(asset) {
  return asset.thumbnail_url || asset.library_url || "";
}

function formatScore(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(4) : "n/a";
}

function basename(path) {
  const text = String(path || "").trim();
  if (!text) {
    return "";
  }
  return text.split(/[\\/]/).filter(Boolean).pop() || text;
}

function isUuidLike(value) {
  const text = String(value || "").trim();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.[a-z0-9]+)?$/i.test(text);
}

function assetDisplayName(asset, index) {
  const sourceName = basename(asset.source_records?.[0]?.source_path);
  if (sourceName && !isUuidLike(sourceName)) {
    return sourceName;
  }
  const libraryName = basename(asset.library_path || asset.path);
  if (libraryName && !isUuidLike(libraryName)) {
    return libraryName;
  }
  return `Asset ${String(index + 1).padStart(2, "0")}`;
}

function assetDimensions(asset) {
  if (!asset.width || !asset.height) {
    return "";
  }
  return `${asset.width} x ${asset.height}`;
}

function mediaTypeLabel(asset) {
  if (asset.media_type) {
    return asset.media_type;
  }
  const name = basename(asset.library_path || asset.path);
  const extension = name.includes(".") ? name.split(".").pop() : "";
  return extension ? extension.toUpperCase() : "media";
}

function assetInfoLabel(asset) {
  const dimensions = assetDimensions(asset);
  const media = mediaTypeLabel(asset);
  return dimensions ? `${dimensions} | ${media}` : media;
}

function assetIndexById(assetId) {
  const assets = state.assetSummary?.assets || [];
  const index = assets.findIndex((asset) => asset.asset_id === assetId);
  return index >= 0 ? index : 0;
}

function selectedAssetDisplayName(asset) {
  return assetDisplayName(asset, assetIndexById(asset.asset_id));
}

function renderLibrarySelectionLayout() {
  const workspace = byId("libraryWorkspace");
  if (!workspace) {
    return;
  }
  workspace.classList.toggle("has-selection", Boolean(state.selectedAsset));
}

function clearSelectedAsset() {
  state.selectedAsset = null;
  updateSimilarSelection(null);
  renderAssets();
  renderAssetDetail();
}

function renderPanelMessage(targetId, message, variant = "empty", actionText = "") {
  const container = byId(targetId);
  if (!container) {
    return;
  }
  container.innerHTML = `
    <div class="state-message ${escapeHtml(`state-${variant}`)}">
      <strong>${escapeHtml(message)}</strong>
      ${actionText ? `<span>${escapeHtml(actionText)}</span>` : ""}
    </div>
  `;
}

function renderSkeletonCards(targetId, count = 6) {
  const container = byId(targetId);
  if (!container) {
    return;
  }
  container.innerHTML = Array.from(
    { length: count },
    () => `
      <div class="skeleton-card">
        <div class="skeleton-media"></div>
        <div class="skeleton-line wide"></div>
        <div class="skeleton-line"></div>
      </div>
    `
  ).join("");
}

function renderCardMeta(info, metric) {
  return `
    <div class="card-meta">
      <span>${escapeHtml(info || "media")}</span>
      <span>${escapeHtml(metric || "ready")}</span>
    </div>
  `;
}

function renderAssets() {
  const assets = state.assetSummary?.assets || [];
  const container = byId("assetGrid");
  const activeRecipe = state.assetSummary?.active_recipe_label || "none";
  setText("assetStats", `${assets.length} assets | active recipe: ${activeRecipe}`);
  renderAssetSelectionControls(assets);
  container.innerHTML = "";

  if (!assets.length) {
    renderPanelMessage("assetGrid", "No assets yet.", "empty", "Import a folder from Import & Runtime to build the wall.");
    return;
  }

  assets.forEach((asset, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "asset-card";
    if (state.selectedAsset?.asset_id === asset.asset_id) {
      button.classList.add("is-selected");
    }
    if (state.selectedAssetIds.has(asset.asset_id)) {
      button.classList.add("is-batch-selected");
    }
    const preview = assetPreviewUrl(asset);
    const title = assetDisplayName(asset, index);
    button.innerHTML = `
      <span class="asset-select"><input type="checkbox" aria-label="Select ${escapeHtml(title)}" ${state.selectedAssetIds.has(asset.asset_id) ? "checked" : ""} /></span>
      ${preview ? `<img src="${preview}" alt="${escapeHtml(title)} preview" />` : `<div class="preview-media"></div>`}
      <div class="asset-card-body">
        <h4>${escapeHtml(title)}</h4>
        ${renderCardMeta(assetInfoLabel(asset), asset.status)}
      </div>
    `;
    button.addEventListener("click", (event) => {
      if (event.target.closest(".asset-select")) {
        state.selectedAssetIds.has(asset.asset_id)
          ? state.selectedAssetIds.delete(asset.asset_id)
          : state.selectedAssetIds.add(asset.asset_id);
        renderAssets();
        return;
      }
      loadAssetDetail(asset.asset_id);
    });
    container.appendChild(button);
  });
}

function renderAssetSelectionControls(assets) {
  const selectedCount = state.selectedAssetIds.size;
  setText("assetSelectionStatus", selectedCount ? `${selectedCount} selected` : "No assets selected");
  byId("selectAllAssetsBtn").disabled = !assets.length || selectedCount === assets.length;
  byId("clearAssetSelectionBtn").disabled = !selectedCount;
  byId("rebuildSelectedAssetsBtn").disabled = !selectedCount;
  byId("deleteSelectedAssetsBtn").disabled = !selectedCount;
}

function renderWorkbenchOverview() {
  const assets = state.assetSummary?.assets || [];
  const jobCounts = state.libraryStatus?.job_counts || {};
  const pendingCount = Number(jobCounts.pending || state.pendingJobs.length || 0);
  const runningCount = Number(jobCounts.running || 0);
  const workerLoop = state.workerLoop || {};
  const importTask = state.importTask || {};
  const recipe = state.assetSummary?.active_recipe_label || "No active recipe";

  setText("activeRecipe", recipe);
  setText("navAssetCount", assets.length);
  setText("navQueueCount", pendingCount + runningCount);
  setText("headingAssetCount", assets.length);
  setText("headingPendingCount", pendingCount);

  const queueTitle = byId("queueMonitorTitle");
  const queueJobs = byId("queueMonitorJobs");
  const queueWorker = byId("queueMonitorWorker");
  const queueProgress = byId("queueMonitorProgress");
  const queueNext = byId("queueMonitorNext");
  const queueAction = byId("queueMonitorAction");
  if (!queueTitle || !queueJobs || !queueWorker || !queueProgress || !queueNext || !queueAction) {
    return;
  }

  const paused = Boolean(workerLoop.paused);
  const queueTotal = pendingCount + runningCount;
  queueTitle.textContent = paused ? "Index worker is paused" : queueTotal ? "Index queue is active" : "Index queue is clear";
  queueJobs.textContent = String(queueTotal);
  queueWorker.textContent = paused ? "Paused" : workerLoop.running ? "Running" : "Offline";
  queueProgress.style.width = queueTotal ? `${Math.max(12, Math.min(100, (runningCount / queueTotal) * 100))}%` : "0%";
  const nextJob = state.pendingJobs[0];
  queueNext.textContent = importTask.running
    ? (importTask.paused ? "Import paused between files." : "Import is adding work to the local queue.")
    : nextJob
    ? `Next: ${nextJob.type} / ${nextJob.asset_id || "library task"}`
    : paused
      ? "Resume the worker to continue queued work."
      : "No pending jobs in the local queue.";
  queueAction.disabled = !workerLoop.running && !paused;
  queueAction.textContent = paused ? "Resume queue" : "Pause queue";
}

function renderAssetDetail() {
  const container = byId("assetDetail");
  if (!state.selectedAsset) {
    container.className = "detail-empty";
    container.textContent = "Select an asset from the library grid.";
    renderLibrarySelectionLayout();
    return;
  }

  const asset = state.selectedAsset;
  container.className = "";
  const title = selectedAssetDisplayName(asset);
  const indexedRecipeRows = (asset.indexed_recipe_labels || [])
    .map((label) => `<span class="chip">${escapeHtml(label)}</span>`)
    .join("");
  const staleRecipeRows = (asset.stale_recipe_labels || [])
    .map((label) => `<span class="chip">${escapeHtml(label)}</span>`)
    .join("");
  const renditionRows = (asset.renditions || [])
    .map((rendition) => {
      const size = rendition.width && rendition.height ? `${rendition.width}x${rendition.height}` : "unknown";
      return `
        <div class="detail-inline-item">
          <strong>${escapeHtml(rendition.kind)}</strong>
          <div class="small muted">${escapeHtml(size)}${rendition.frame_index != null ? ` | frame ${escapeHtml(rendition.frame_index)}` : ""}</div>
        </div>
      `;
    })
    .join("");
  const technicalRenditionRows = (asset.renditions || [])
    .map((rendition) => {
      const size = rendition.width && rendition.height ? `${rendition.width}x${rendition.height}` : "unknown";
      return `
        <div class="mono small">
          ${escapeHtml(rendition.kind)} | ${escapeHtml(rendition.path)} | ${escapeHtml(size)}
        </div>
      `;
    })
    .join("");
  const ocrRows = (asset.ocr_results || [])
    .map((result) => {
      const confidence = result.confidence == null ? "n/a" : Number(result.confidence).toFixed(3);
      const ocrText = result.text || "No OCR text detected.";
      const hasEncodingDamage = String(result.text || "").includes("\uFFFD");
      return `
        <article class="ocr-result-card">
          <div class="ocr-result-meta">
            <span>${escapeHtml(result.engine)} ${escapeHtml(result.engine_version || "")}</span>
            <span class="chip">confidence ${escapeHtml(confidence)}</span>
          </div>
          ${hasEncodingDamage ? `<div class="ocr-encoding-warning">This stored result was damaged by the previous Windows text encoding. OCR repair has been queued.</div>` : ""}
          <pre class="ocr-text">${escapeHtml(ocrText)}</pre>
        </article>
      `;
    })
    .join("");
  const sourceRecordRows = (asset.source_records || [])
    .map(
      (record) => `
        <article class="source-record-card">
          <div class="source-record-meta">
            <div class="small"><strong>${escapeHtml(basename(record.source_path) || "source file")}</strong></div>
            <div class="mono small muted">${escapeHtml(record.source_path)}</div>
            <div class="small muted">imported ${escapeHtml(formatDate(record.imported_at))} | last seen ${escapeHtml(formatDate(record.last_seen_at))}</div>
          </div>
          <div class="inline-button-stack">
            <button type="button" class="ghost inline-action reveal-source-btn" data-asset-id="${escapeHtml(asset.asset_id)}" data-source-path="${escapeHtml(record.source_path)}">Reveal Source</button>
            <button type="button" class="ghost inline-action remove-source-btn" data-asset-id="${escapeHtml(asset.asset_id)}" data-source-path="${escapeHtml(record.source_path)}">Remove Source</button>
          </div>
        </article>
      `
    )
    .join("");
  const jobRows = (asset.jobs || [])
    .map(
      (job) => `
        <div class="detail-inline-item">
          <strong>${escapeHtml(job.type)}</strong>
          <div class="small muted">${escapeHtml(job.status)} | attempts ${escapeHtml(job.attempt_count)}</div>
        </div>
      `
    )
    .join("");

  container.innerHTML = `
    <div class="asset-detail-shell">
      <div class="asset-detail-preview-frame">
        <img class="asset-detail-preview" src="${asset.library_url}" alt="${escapeHtml(title)} preview" />
      </div>

      <section class="asset-detail-summary">
        <h3>${escapeHtml(title)}</h3>
        <div class="asset-meta">
          <span class="chip">${escapeHtml(asset.media_type)}</span>
          <span class="chip">${escapeHtml(asset.status)}</span>
          <span class="chip">${escapeHtml(asset.ocr_status || "missing")} OCR</span>
          <span class="chip">${escapeHtml(asset.width || "?")}x${escapeHtml(asset.height || "?")}</span>
        </div>
        <div class="asset-detail-stats">
          <div class="detail-kv">
            <span>Imported</span>
            <strong>${escapeHtml(formatDate(asset.imported_at))}</strong>
          </div>
          <div class="detail-kv">
            <span>Updated</span>
            <strong>${escapeHtml(formatDate(asset.updated_at))}</strong>
          </div>
          <div class="detail-kv">
            <span>Source Records</span>
            <strong>${escapeHtml(asset.source_record_count || 0)}</strong>
          </div>
          <div class="detail-kv">
            <span>Jobs</span>
            <strong>${escapeHtml((asset.jobs || []).length || 0)}</strong>
          </div>
        </div>
        <div class="actions asset-detail-actions">
          <button type="button" class="ghost reveal-managed-btn" data-asset-id="${escapeHtml(asset.asset_id)}">Reveal Managed File</button>
          <button type="button" class="ghost delete-asset-btn" data-asset-id="${escapeHtml(asset.asset_id)}">Delete Asset</button>
        </div>
      </section>

      <div class="detail-section">
        <strong>Source Records</strong>
        <div class="source-record-list">${sourceRecordRows || `<div class="detail-empty">No source records available.</div>`}</div>
      </div>

      <div class="detail-section">
        <strong>Recipe Coverage</strong>
        <div class="recipe-state-grid">
          <div class="recipe-state-card">
            <strong>Indexed Recipes</strong>
            <div>${indexedRecipeRows || `<div class="small muted">none</div>`}</div>
          </div>
          <div class="recipe-state-card">
            <strong>Stale Recipes</strong>
            <div>${staleRecipeRows || `<div class="small muted">none</div>`}</div>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <strong>Renditions</strong>
        <div class="detail-inline-list">${renditionRows || `<div class="detail-empty">No renditions recorded.</div>`}</div>
      </div>

      <div class="detail-section">
        <strong>Jobs</strong>
        <div class="detail-inline-list">${jobRows || `<div class="detail-empty">No jobs recorded.</div>`}</div>
      </div>

      <details class="detail-section technical-info detail-disclosure">
          <summary>Technical Information</summary>
          <div class="technical-grid">
            <div>
              <strong>Asset ID</strong>
              <div class="mono small">${escapeHtml(asset.asset_id)}</div>
            </div>
            <div>
              <strong>Library Path</strong>
              <div class="mono small">${escapeHtml(asset.library_path)}</div>
            </div>
            <div>
              <strong>Content Hash</strong>
              <div class="mono small">${escapeHtml(asset.content_hash)}</div>
            </div>
            <div>
              <strong>Rendition Paths</strong>
              <div>${technicalRenditionRows || "none"}</div>
            </div>
          </div>
      </details>

      <details class="detail-section technical-info detail-disclosure">
        <summary>OCR Text</summary>
        <div>${ocrRows || `<div class="detail-empty">No OCR text available for this asset.</div>`}</div>
      </details>
    </div>
  `;

  container.querySelectorAll(".remove-source-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await removeSourceRecord(button.dataset.assetId, button.dataset.sourcePath);
      } catch (error) {
        showError("assetDetail", error);
      }
    });
  });
  container.querySelectorAll(".reveal-source-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await revealAssetFile(button.dataset.assetId, "source", button.dataset.sourcePath);
      } catch (error) {
        showError("assetDetail", error);
      }
    });
  });
  container.querySelectorAll(".reveal-managed-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await revealAssetFile(button.dataset.assetId, "managed");
      } catch (error) {
        showError("assetDetail", error);
      }
    });
  });
  container.querySelectorAll(".delete-asset-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await deleteAsset(button.dataset.assetId);
      } catch (error) {
        showError("assetDetail", error);
      }
    });
  });
  renderLibrarySelectionLayout();
}

function renderResults(targetId, results, emptyText) {
  const container = byId(targetId);
  container.innerHTML = "";
  if (!results.length) {
    renderPanelMessage(targetId, emptyText, "empty", "Try another query or index more assets.");
    return;
  }

  results.forEach((result, index) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "asset-card result-card";
    const preview = result.thumbnail_url || result.library_url || "";
    const title = assetDisplayName(result, index);
    const sourceLabel = (result.match_sources || [])
      .map((source) => source === "ocr" ? "OCR" : "Visual")
      .join(" + ") || "Visual";
    const snippet = result.ocr_snippet ? `<div class="small muted">${escapeHtml(result.ocr_snippet)}</div>` : "";
    item.innerHTML = `
      ${preview ? `<img src="${preview}" alt="${escapeHtml(title)} preview" />` : `<div class="preview-media"></div>`}
      <div class="asset-card-body">
        <h4>${escapeHtml(title)}</h4>
        ${renderCardMeta(sourceLabel, `score ${formatScore(result.score)}`)}
        ${snippet}
      </div>
    `;
    item.addEventListener("click", async () => {
      await loadAssetDetail(result.asset_id);
      switchTab("libraryTab");
    });
    container.appendChild(item);
  });
}

function renderDuplicatePairs() {
  const container = byId("duplicateResults");
  container.innerHTML = "";
  if (!state.duplicatePairs.length) {
    renderPanelMessage("duplicateResults", "No duplicate pairs found.", "empty", "Lower the threshold or index more assets, then scan again.");
    return;
  }

  state.duplicatePairs.forEach((pair, pairIndex) => {
    const item = document.createElement("div");
    item.className = "duplicate-pair";
    const left = {
      asset_id: pair.asset_a_id,
      thumbnail_url: pair.asset_a_thumbnail_url,
      library_path: pair.asset_a_path,
      matched_source_ref: pair.asset_a_matched_source_ref,
    };
    const right = {
      asset_id: pair.asset_b_id,
      thumbnail_url: pair.asset_b_thumbnail_url,
      library_path: pair.asset_b_path,
      matched_source_ref: pair.asset_b_matched_source_ref,
    };
    item.appendChild(renderDuplicateAssetCard(left, pair.score, pairIndex * 2));
    item.appendChild(renderDuplicateAssetCard(right, pair.score, pairIndex * 2 + 1));
    container.appendChild(item);
  });
}

function renderDuplicateAssetCard(asset, score, index) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "asset-card result-card";
  const preview = asset.thumbnail_url || "";
  const title = assetDisplayName(asset, index);
  button.innerHTML = `
    ${preview ? `<img src="${preview}" alt="${escapeHtml(title)} preview" />` : `<div class="preview-media"></div>`}
    <div class="asset-card-body">
      <h4>${escapeHtml(title)}</h4>
      ${renderCardMeta(mediaTypeLabel(asset), `match ${formatScore(score)}`)}
    </div>
  `;
  button.addEventListener("click", async () => {
    await loadAssetDetail(asset.asset_id);
    switchTab("libraryTab");
  });
  return button;
}

function updateSimilarSelection(asset) {
  const input = byId("similarAssetIdInput");
  const label = byId("similarSelectedAsset");
  if (!input || !label) {
    return;
  }
  if (!asset) {
    input.value = "";
    label.textContent = "No asset selected yet.";
    return;
  }
  input.value = asset.asset_id;
  label.textContent = selectedAssetDisplayName(asset);
}

function appendStatusCards(targetId, entries) {
  const node = byId(targetId);
  node.innerHTML = "";
  if (!entries.length) {
    node.innerHTML = `<div class="detail-empty">No data yet.</div>`;
    return;
  }
  entries.forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "status-card";
    card.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
    node.appendChild(card);
  });
}

function renderPendingJobs() {
  const jobs = state.pendingJobs || [];
  const selectedCount = state.selectedPendingJobIds.size;
  setText(
    "pendingJobSelectionStatus",
    jobs.length
      ? `${selectedCount} of ${jobs.length} pending job(s) selected. Deleting removes only unclaimed queue records.`
      : "No pending jobs."
  );
  byId("deleteSelectedPendingJobsBtn").disabled = selectedCount === 0;
  byId("clearPendingJobSelectionBtn").disabled = selectedCount === 0;
  byId("selectAllPendingJobsBtn").disabled = jobs.length === 0 || selectedCount === jobs.length;

  const node = byId("pendingJobList");
  node.innerHTML = "";
  if (!jobs.length) {
    node.innerHTML = `<div class="detail-empty">No pending jobs in the queue.</div>`;
    return;
  }

  jobs.forEach((job) => {
    const item = document.createElement("label");
    item.className = "pending-job-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedPendingJobIds.has(job.job_id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selectedPendingJobIds.add(job.job_id);
      } else {
        state.selectedPendingJobIds.delete(job.job_id);
      }
      renderPendingJobs();
    });
    const content = document.createElement("div");
    content.className = "pending-job-content";
    content.innerHTML = `
      <div><strong>${escapeHtml(job.type)}</strong> <span class="chip">pending</span></div>
      <div class="result-meta">
        <span>${escapeHtml(job.asset_path || job.asset_id || "no asset")}</span>
        <span>attempts ${escapeHtml(job.attempt_count)}</span>
        <span>${escapeHtml(formatDate(job.created_at))}</span>
      </div>
      <div class="small muted mono">${escapeHtml(job.job_id)}</div>
    `;
    item.append(checkbox, content);
    node.appendChild(item);
  });
}

function renderLibraryStatus() {
  const assetCounts = Object.entries(state.libraryStatus?.asset_counts || {});
  const jobCounts = Object.entries(state.libraryStatus?.job_counts || {});
  const recentJobs = state.libraryStatus?.recent_jobs || [];
  const workerLoop = state.workerLoop || {};

  appendStatusCards("statusSummary", assetCounts);
  appendStatusCards("jobSummary", jobCounts);
  appendStatusCards("workerLoopSummary", [
    ["running", workerLoop.running],
    ["paused", workerLoop.paused],
    ["interval", workerLoop.interval_seconds],
    ["last start", formatDate(workerLoop.last_tick_started_at)],
    ["last finish", formatDate(workerLoop.last_tick_finished_at)],
    ["event log", workerLoop.event_log_path || "n/a"],
  ]);

  const recentJobsNode = byId("recentJobs");
  recentJobsNode.innerHTML = "";
  if (!recentJobs.length) {
    recentJobsNode.innerHTML = `<div class="detail-empty">No jobs recorded yet.</div>`;
  } else {
    recentJobs.forEach((job) => {
      const item = document.createElement("div");
      item.className = "result-item";
      item.innerHTML = `
        <div><strong>${escapeHtml(job.type)}</strong></div>
        <div class="result-meta">
          <span class="chip">${escapeHtml(job.status)}</span>
          <span>${escapeHtml(job.asset_id || "no-asset")}</span>
        </div>
        <div class="result-meta">
          <span>attempts ${escapeHtml(job.attempt_count)}</span>
          <span>${escapeHtml(formatDate(job.updated_at))}</span>
        </div>
        ${job.error_detail ? `<pre class="console">${escapeHtml(job.error_detail)}</pre>` : ""}
      `;
      recentJobsNode.appendChild(item);
    });
  }

  const eventsNode = byId("workerLoopEvents");
  eventsNode.innerHTML = "";
  const events = workerLoop.recent_events || [];
  if (!events.length) {
    eventsNode.innerHTML = `<div class="detail-empty">No worker loop events yet.</div>`;
  } else {
    events.forEach((event) => {
      const item = document.createElement("div");
      item.className = "result-item";
      item.innerHTML = `
        <div><strong>${escapeHtml(event.event)}</strong></div>
        <div class="result-meta">
          <span>${escapeHtml(formatDate(event.timestamp))}</span>
        </div>
        <pre class="console">${escapeHtml(JSON.stringify(event.payload || {}, null, 2))}</pre>
      `;
      eventsNode.appendChild(item);
    });
  }

  const persistedEventsNode = byId("workerLoopPersistedEvents");
  persistedEventsNode.innerHTML = "";
  const persistedEvents = workerLoop.persisted_events || [];
  if (!persistedEvents.length) {
    persistedEventsNode.innerHTML = `<div class="detail-empty">No persisted worker events yet.</div>`;
  } else {
    persistedEvents.forEach((event) => {
      const item = document.createElement("div");
      item.className = "result-item";
      item.innerHTML = `
        <div><strong>${escapeHtml(event.event)}</strong></div>
        <div class="result-meta">
          <span>${escapeHtml(formatDate(event.timestamp))}</span>
        </div>
        <pre class="console">${escapeHtml(JSON.stringify(event.payload || {}, null, 2))}</pre>
      `;
      persistedEventsNode.appendChild(item);
    });
  }
}

async function loadState() {
  renderSkeletonCards("assetGrid", 8);
  const payload = await api("/api/state");
  state.runtimeProfiles = payload.runtime_profiles || [];
  state.modelVariants = payload.model_variants || [];
  state.runtimeSettings = payload.runtime_settings || null;
  state.setupState = payload.setup_state || null;
  state.assetSummary = payload.asset_summary || null;
  const availableAssetIds = new Set((state.assetSummary?.assets || []).map((asset) => asset.asset_id));
  state.selectedAssetIds = new Set([...state.selectedAssetIds].filter((assetId) => availableAssetIds.has(assetId)));
  state.libraryStatus = payload.library_status || null;
  state.workerLoop = payload.worker_loop || null;
  state.importTask = payload.import_task || null;
  state.pendingJobs = payload.pending_jobs || [];
  const availablePendingJobIds = new Set(state.pendingJobs.map((job) => job.job_id));
  state.selectedPendingJobIds = new Set(
    [...state.selectedPendingJobIds].filter((jobId) => availablePendingJobIds.has(jobId))
  );
  state.lastHealthDiagnosticSteps = state.setupState?.runtime_readiness?.last_health_diagnostic_steps || [];
  if (
    state.selectedAsset &&
    !(state.assetSummary?.assets || []).some((asset) => asset.asset_id === state.selectedAsset.asset_id)
  ) {
    state.selectedAsset = null;
  }
  renderProfiles();
  renderModelVariants();
  renderRuntimeSummary();
  renderSetupGuide();
  renderSetupChecklist();
  renderRuntimeReadiness();
  renderHealthDiagnostics();
  renderAssets();
  renderAssetDetail();
  renderLibraryStatus();
  renderPendingJobs();
  renderWorkbenchOverview();
  renderImportTask();
  updateImportPolling();
}

async function loadAssetDetail(assetId) {
  const result = await api(`/api/asset-detail?asset_id=${encodeURIComponent(assetId)}`);
  state.selectedAsset = result.asset;
  updateSimilarSelection(result.asset);
  renderAssets();
  renderAssetDetail();
}

async function removeSourceRecord(assetId, sourcePath) {
  const result = await api("/api/remove-source-record", {
    method: "POST",
    body: JSON.stringify({
      asset_id: assetId,
      source_path: sourcePath,
    }),
  });
  await loadState();
  if (result.asset_deleted) {
    clearSelectedAsset();
    return;
  }
  await loadAssetDetail(assetId);
}

async function deleteAsset(assetId) {
  await api("/api/delete-asset", {
    method: "POST",
    body: JSON.stringify({
      asset_id: assetId,
    }),
  });
  state.selectedAsset = null;
  await loadState();
}

async function runBatchAssetAction(action) {
  const assetIds = [...state.selectedAssetIds];
  if (!assetIds.length) {
    return;
  }
  if (action === "delete" && !window.confirm(`Delete ${assetIds.length} selected asset(s)? This also deletes their library copies and derived artifacts.`)) {
    return;
  }
  const result = await api("/api/assets/batch-action", {
    method: "POST",
    body: JSON.stringify({ action, asset_ids: assetIds }),
  });
  state.selectedAssetIds.clear();
  state.selectedAsset = null;
  await loadState();
  setText("assetSelectionStatus", action === "delete"
    ? `Deleted ${result.affected_asset_ids.length} asset(s).`
    : `Queued ${result.reindex_jobs_created} active-index rebuild(s); skipped ${result.skipped_running_asset_ids.length} running asset(s).`);
}

async function revealAssetFile(assetId, target = "managed", sourcePath = null) {
  await api("/api/reveal-asset-file", {
    method: "POST",
    body: JSON.stringify({
      asset_id: assetId,
      target,
      source_path: sourcePath,
    }),
  });
}

async function saveSettings() {
  const payload = {
    selected_profile: byId("profileSelect").value,
    selected_model_key: byId("modelVariantSelect").value,
    model_name_or_path: byId("modelPathInput").value.trim() || null,
    gif_frame_count: Number(byId("gifFrameCountInput").value),
    backend_name: selectedBackendName(),
  };
  const result = await api("/api/runtime-settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.runtimeSettings = result.runtime_settings || null;
  renderRuntimeSummary();
}

function buildFirstRunPayload(importPath = null) {
  return {
    selected_profile: byId("profileSelect").value,
    selected_model_key: byId("modelVariantSelect").value,
    model_name_or_path: byId("modelPathInput").value.trim() || null,
    gif_frame_count: Number(byId("gifFrameCountInput").value),
    import_path: importPath,
    backend_name: selectedBackendName(),
  };
}

async function runFirstRunFlow(importPath = null) {
  const result = await api("/api/first-run", {
    method: "POST",
    body: JSON.stringify(buildFirstRunPayload(importPath)),
  });
  state.lastHealthDiagnosticSteps = result.health_check?.diagnostic_steps || [];
  state.workerLoop = result.worker_loop || state.workerLoop;
  byId("modelPathInput").value = "";
  byId("healthResult").textContent = JSON.stringify(result.health_check || result, null, 2);
  byId("importResult").textContent = JSON.stringify(result, null, 2);
  await loadState();
}

async function prepareRuntime() {
  await runFirstRunFlow(null);
}

async function completeFirstRun() {
  await runFirstRunFlow(byId("importPathInput").value.trim() || null);
}

async function runHealthCheck() {
  await saveSettings();
  const payload = {
    profile_id: byId("profileSelect").value,
    model_key: byId("modelVariantSelect").value,
    model_name_or_path: byId("modelPathInput").value.trim() || null,
  };
  const result = await api("/api/health", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.lastHealthDiagnosticSteps = result.diagnostic_steps || [];
  byId("healthResult").textContent = JSON.stringify(result, null, 2);
  byId("modelPathInput").value = "";
  await loadState();
}

function renderImportTask() {
  const task = state.importTask || {};
  const running = Boolean(task.running);
  const paused = Boolean(task.paused);
  const pauseRequested = Boolean(task.pause_requested);
  const node = byId("importTaskStatus");
  if (node) {
    if (!running && !task.status) {
      node.textContent = "No import is running.";
    } else if (running) {
      node.textContent = paused
        ? "Import paused. Resume to continue with the next file."
        : pauseRequested
          ? "Pausing import after the current file finishes safely."
          : `Importing ${task.source_folder || "folder"}. Pause takes effect between files.`;
    } else if (task.status === "completed") {
      node.textContent = `Import completed: ${task.result?.new_assets || 0} new asset(s), ${task.result?.duplicate_assets || 0} duplicate(s).`;
    } else if (task.status === "failed" || task.status === "cancelled") {
      node.textContent = `Import ${task.status}: ${task.error?.detail || "unknown error"}`;
    } else {
      node.textContent = "No import is running.";
    }
  }
  byId("pauseImportBtn").disabled = !running || pauseRequested;
  byId("resumeImportBtn").disabled = !running || !pauseRequested;
}

function updateImportPolling() {
  if (state.importTask?.running && !state.importPollTimer) {
    state.importPollTimer = window.setInterval(async () => {
      try {
        state.importTask = await api("/api/import");
        renderImportTask();
        if (!state.importTask.running) {
          window.clearInterval(state.importPollTimer);
          state.importPollTimer = null;
          await loadState();
        }
      } catch (error) {
        window.clearInterval(state.importPollTimer);
        state.importPollTimer = null;
        showError("importResult", error);
      }
    }, 700);
  }
}

async function startImport(startIndexing) {
  state.importTask = await api("/api/import/start", {
    method: "POST",
    body: JSON.stringify({
      path: byId("importPathInput").value.trim(),
      start_indexing: startIndexing,
    }),
  });
  byId("importResult").textContent = "Import started in the background.";
  renderImportTask();
  updateImportPolling();
}

async function importFolder() {
  await startImport(false);
}

async function pickImportFolder() {
  const result = await api("/api/pick-folder", {
    method: "POST",
    body: JSON.stringify({
      title: "Choose a folder to import into MemeSort",
      initial_path: byId("importPathInput").value.trim() || null,
    }),
  });
  if (result.selected_path) {
    byId("importPathInput").value = result.selected_path;
  }
}

async function pickModelPath() {
  const result = await api("/api/pick-folder", {
    method: "POST",
    body: JSON.stringify({
      title: "Choose a local model folder",
      initial_path: byId("modelPathInput").value.trim() || null,
    }),
  });
  if (result.selected_path) {
    byId("modelPathInput").value = result.selected_path;
  }
}

async function pickImageSearchFile() {
  const result = await api("/api/pick-file", {
    method: "POST",
    body: JSON.stringify({
      title: "Choose an image or GIF to search against MemeSort",
      initial_path: byId("imageSearchPathInput").value.trim() || null,
      filter_string: "Image Files|*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp|All Files|*.*",
    }),
  });
  if (result.selected_path) {
    byId("imageSearchPathInput").value = result.selected_path;
  }
}

async function importAndStartIndexing() {
  await startImport(true);
}

async function importCommand(command) {
  state.importTask = await api(`/api/import/${command}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  renderImportTask();
  updateImportPolling();
}

async function runJobs() {
  const result = await api("/api/run-jobs", {
    method: "POST",
    body: JSON.stringify({
      max_jobs: 50,
    }),
  });
  byId("importResult").textContent = JSON.stringify(result, null, 2);
  await loadState();
}

async function runSearch() {
  const query = byId("searchInput").value.trim();
  const requestId = crypto.randomUUID();
  state.activeSearchRequestIds.add(requestId);
  renderSkeletonCards("searchResults", 4);
  try {
    const result = await api(
      `/api/search?query=${encodeURIComponent(query)}&top_k=18&request_id=${encodeURIComponent(requestId)}`
    );
    renderResults("searchResults", result.results || [], "No matches yet.");
  } finally {
    state.activeSearchRequestIds.delete(requestId);
  }
}

async function runImageSearch() {
  const requestId = crypto.randomUUID();
  state.activeSearchRequestIds.add(requestId);
  renderSkeletonCards("imageSearchResults", 4);
  try {
    const result = await api("/api/search-image", {
      method: "POST",
      body: JSON.stringify({
        path: byId("imageSearchPathInput").value.trim(),
        top_k: 18,
        request_id: requestId,
      }),
    });
    renderResults("imageSearchResults", result.results || [], "No image matches found.");
  } finally {
    state.activeSearchRequestIds.delete(requestId);
  }
}

async function runSimilar() {
  const assetId = byId("similarAssetIdInput").value.trim();
  if (!assetId) {
    renderPanelMessage("similarResults", "No asset selected.", "empty", "Open an asset detail first, then run similarity.");
    return;
  }
  renderSkeletonCards("similarResults", 4);
  const result = await api(`/api/find-similar?asset_id=${encodeURIComponent(assetId)}&top_k=18`);
  renderResults("similarResults", result.results || [], "No similar assets found.");
}

async function scanDuplicates() {
  const threshold = Number(byId("duplicateThresholdInput").value);
  renderSkeletonCards("duplicateResults", 4);
  const result = await api(`/api/duplicates?threshold=${encodeURIComponent(threshold)}`);
  state.duplicatePairs = result.pairs || [];
  renderDuplicatePairs();
}

async function refreshLibraryStatus() {
  const [libraryStatus, workerLoop, pendingJobs] = await Promise.all([
    api("/api/library-status"),
    api("/api/worker-loop"),
    api("/api/pending-jobs"),
  ]);
  state.libraryStatus = libraryStatus;
  state.workerLoop = workerLoop;
  state.pendingJobs = pendingJobs.jobs || [];
  const availablePendingJobIds = new Set(state.pendingJobs.map((job) => job.job_id));
  state.selectedPendingJobIds = new Set(
    [...state.selectedPendingJobIds].filter((jobId) => availablePendingJobIds.has(jobId))
  );
  renderLibraryStatus();
  renderPendingJobs();
  renderWorkbenchOverview();
}

async function workerLoopCommand(command) {
  state.workerLoop = await api(`/api/worker-loop/${command}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  renderLibraryStatus();
  renderWorkbenchOverview();
}

async function retryFailedJobs() {
  await api("/api/retry-failed-jobs", {
    method: "POST",
    body: JSON.stringify({}),
  });
  await refreshLibraryStatus();
}

async function deleteSelectedPendingJobs() {
  const jobIds = [...state.selectedPendingJobIds];
  if (!jobIds.length) {
    return;
  }
  if (!window.confirm(`Delete ${jobIds.length} pending job(s)? Assets and generated files will not be deleted.`)) {
    return;
  }
  const result = await api("/api/pending-jobs/delete", {
    method: "POST",
    body: JSON.stringify({ job_ids: jobIds }),
  });
  state.selectedPendingJobIds.clear();
  await refreshLibraryStatus();
  byId("pendingJobSelectionStatus").textContent =
    `Deleted ${result.deleted_job_ids.length} pending job(s); skipped ${result.skipped_job_ids.length} that were already claimed or absent.`;
}

function showError(targetId, error) {
  const node = byId(targetId);
  if (!node) {
    return;
  }
  if (node.tagName === "PRE") {
    node.textContent = String(error);
    return;
  }
  renderPanelMessage(targetId, "Action failed.", "error", String(error));
}

function restoreResizablePanelWidths() {
  [
    {
      storageKey: LIBRARY_DETAIL_WIDTH_KEY,
      variableName: "--library-detail-width",
      fallbackWidth: 420,
      minWidth: 340,
      maxWidth: 640,
    },
  ].forEach((config) => {
    let width = config.fallbackWidth;
    try {
      const stored = Number(window.localStorage.getItem(config.storageKey));
      if (Number.isFinite(stored)) {
        width = clampNumber(stored, config.minWidth, config.maxWidth);
      }
    } catch {
      width = config.fallbackWidth;
    }
    setCssWidthVariable(config.variableName, width);
  });
}

function attachResizablePanel({
  handleId,
  containerId,
  variableName,
  storageKey,
  minWidth,
  maxWidth,
  isEnabled = () => true,
}) {
  const handle = byId(handleId);
  const container = byId(containerId);
  if (!handle || !container) {
    return;
  }

  let activePointerId = null;

  const updateWidthFromPointer = (event) => {
    if (event.pointerId !== activePointerId) {
      return;
    }
    const rect = container.getBoundingClientRect();
    const width = clampNumber(rect.right - event.clientX, minWidth, maxWidth);
    setCssWidthVariable(variableName, width);
  };

  const stopResize = (event) => {
    if (event.pointerId !== activePointerId) {
      return;
    }
    document.body.classList.remove("is-resizing-panels");
    handle.classList.remove("is-resizing");
    try {
      const resolvedWidth = Number.parseInt(
        getComputedStyle(document.documentElement).getPropertyValue(variableName),
        10
      );
      if (Number.isFinite(resolvedWidth)) {
        window.localStorage.setItem(storageKey, String(resolvedWidth));
      }
    } catch {
      // Ignore persistence errors and keep the in-memory width.
    }
    handle.releasePointerCapture?.(activePointerId);
    activePointerId = null;
    window.removeEventListener("pointermove", updateWidthFromPointer);
    window.removeEventListener("pointerup", stopResize);
    window.removeEventListener("pointercancel", stopResize);
  };

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || isStackedSidebarLayout() || !isEnabled()) {
      return;
    }
    activePointerId = event.pointerId;
    document.body.classList.add("is-resizing-panels");
    handle.classList.add("is-resizing");
    handle.setPointerCapture?.(activePointerId);
    window.addEventListener("pointermove", updateWidthFromPointer);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
    updateWidthFromPointer(event);
    event.preventDefault();
  });
}

function initResizablePanels() {
  restoreResizablePanelWidths();
  attachResizablePanel({
    handleId: "libraryDetailResizeHandle",
    containerId: "libraryWorkspace",
    variableName: "--library-detail-width",
    storageKey: LIBRARY_DETAIL_WIDTH_KEY,
    minWidth: 340,
    maxWidth: 640,
    isEnabled: () => Boolean(state.selectedAsset),
  });
}

function wireEvents() {
  byId("themeToggle").addEventListener("click", () => {
    applyTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
  });
  byId("refreshState").addEventListener("click", () => {
    loadState().catch((error) => showError("healthResult", error));
  });
  byId("reloadAssetsBtn").addEventListener("click", () => {
    loadState().catch((error) => showError("assetDetail", error));
  });
  byId("closeAssetDetailBtn").addEventListener("click", () => {
    clearSelectedAsset();
  });
  byId("profileSelect").addEventListener("change", () => {
    if (selectedBackendName() !== (state.runtimeSettings?.backend_name || "qwen3-vl")) {
      byId("modelPathInput").value = "";
    }
    syncSetupSelections(true);
  });
  byId("modelVariantSelect").addEventListener("change", () => {
    if (byId("modelVariantSelect").value !== state.runtimeSettings?.selected_model_key) {
      byId("modelPathInput").value = "";
    }
    syncSetupSelections(true);
  });
  byId("saveSettingsBtn").addEventListener("click", async () => {
    try {
      await saveSettings();
      await loadState();
    } catch (error) {
      showError("healthResult", error);
    }
  });
  byId("prepareRuntimeBtn").addEventListener("click", async () => {
    try {
      await prepareRuntime();
    } catch (error) {
      showError("healthResult", error);
    }
  });
  byId("healthCheckBtn").addEventListener("click", async () => {
    try {
      await runHealthCheck();
    } catch (error) {
      showError("healthResult", error);
    }
  });
  byId("importBtn").addEventListener("click", async () => {
    try {
      await importFolder();
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("pickImportFolderBtn").addEventListener("click", async () => {
    try {
      await pickImportFolder();
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("pickModelPathBtn").addEventListener("click", async () => {
    try {
      await pickModelPath();
    } catch (error) {
      showError("healthResult", error);
    }
  });
  byId("completeFirstRunBtn").addEventListener("click", async () => {
    try {
      await completeFirstRun();
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("importAndIndexBtn").addEventListener("click", async () => {
    try {
      await importAndStartIndexing();
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("runJobsBtn").addEventListener("click", async () => {
    try {
      await runJobs();
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("pauseImportBtn").addEventListener("click", async () => {
    try {
      await importCommand("pause");
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("resumeImportBtn").addEventListener("click", async () => {
    try {
      await importCommand("resume");
    } catch (error) {
      showError("importResult", error);
    }
  });
  byId("searchBtn").addEventListener("click", async () => {
    try {
      await runSearch();
    } catch (error) {
      showError("searchResults", error);
    }
  });
  byId("pickImageSearchFileBtn").addEventListener("click", async () => {
    try {
      await pickImageSearchFile();
    } catch (error) {
      showError("imageSearchResults", error);
    }
  });
  byId("searchImageBtn").addEventListener("click", async () => {
    try {
      await runImageSearch();
    } catch (error) {
      showError("imageSearchResults", error);
    }
  });
  byId("similarBtn").addEventListener("click", async () => {
    try {
      await runSimilar();
    } catch (error) {
      showError("similarResults", error);
    }
  });
  byId("scanDuplicatesBtn").addEventListener("click", async () => {
    try {
      await scanDuplicates();
    } catch (error) {
      showError("duplicateResults", error);
    }
  });
  byId("refreshStatusBtn").addEventListener("click", async () => {
    try {
      await refreshLibraryStatus();
    } catch (error) {
      showError("recentJobs", error);
    }
  });
  byId("resumeLoopBtn").addEventListener("click", async () => {
    try {
      await workerLoopCommand("resume");
    } catch (error) {
      showError("workerLoopEvents", error);
    }
  });
  byId("pauseLoopBtn").addEventListener("click", async () => {
    try {
      await workerLoopCommand("pause");
    } catch (error) {
      showError("workerLoopEvents", error);
    }
  });
  byId("retryFailedJobsBtn").addEventListener("click", async () => {
    try {
      await retryFailedJobs();
    } catch (error) {
      showError("recentJobs", error);
    }
  });
  byId("selectAllPendingJobsBtn").addEventListener("click", () => {
    state.selectedPendingJobIds = new Set(state.pendingJobs.map((job) => job.job_id));
    renderPendingJobs();
  });
  byId("clearPendingJobSelectionBtn").addEventListener("click", () => {
    state.selectedPendingJobIds.clear();
    renderPendingJobs();
  });
  byId("deleteSelectedPendingJobsBtn").addEventListener("click", async () => {
    try {
      await deleteSelectedPendingJobs();
    } catch (error) {
      showError("pendingJobList", error);
    }
  });
  byId("reloadPendingJobsBtn").addEventListener("click", async () => {
    try {
      await refreshLibraryStatus();
    } catch (error) {
      showError("pendingJobList", error);
    }
  });
  byId("selectAllAssetsBtn").addEventListener("click", () => {
    state.selectedAssetIds = new Set((state.assetSummary?.assets || []).map((asset) => asset.asset_id));
    renderAssets();
  });
  byId("clearAssetSelectionBtn").addEventListener("click", () => {
    state.selectedAssetIds.clear();
    renderAssets();
  });
  byId("deleteSelectedAssetsBtn").addEventListener("click", async () => {
    try { await runBatchAssetAction("delete"); } catch (error) { showError("assetGrid", error); }
  });
  byId("rebuildSelectedAssetsBtn").addEventListener("click", async () => {
    try { await runBatchAssetAction("rebuild-active-index"); } catch (error) { showError("assetGrid", error); }
  });
  byId("triggerLoopBtn").addEventListener("click", async () => {
    try {
      await workerLoopCommand("trigger");
    } catch (error) {
      showError("workerLoopEvents", error);
    }
  });
  byId("queueMonitorAction").addEventListener("click", async () => {
    try {
      await workerLoopCommand(state.workerLoop?.paused ? "resume" : "pause");
    } catch (error) {
      showError("assetGrid", error);
    }
  });
  document.querySelectorAll(".tab-link").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
}

initializeTheme();
initResizablePanels();
wireEvents();
loadState().catch((error) => {
  document.body.innerHTML = `<pre class="console">${escapeHtml(String(error))}</pre>`;
});
