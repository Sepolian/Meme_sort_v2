import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import { tauriErrorDetail } from "./api/tauri-error";
import type { AppState, DuplicatePair, SearchAsset } from "./api/types";
import { mediaUrl } from "./api/media-url";
import { EmptyState, LoadingState, RuntimeNotReady, SidecarDisconnected } from "./components/States";
import { AssetsWorkspace } from "./features/assets/AssetsWorkspace";
import { LibraryImportMenu } from "./features/library/LibraryImportMenu";
import { LibraryShell } from "./features/library/LibraryShell";
import { useLibraryUrlState } from "./features/library/useLibraryUrlState";
import { SettingsPage } from "./features/settings/SettingsPage";
import { ImportBatchProvider } from "./features/import/ImportBatchProvider";
import { ImportBatchPanel } from "./features/import/ImportBatchPanel";
import { useImportBatch } from "./features/import/ImportBatchContext";
import { RuntimeHealthProvider, useRuntimeHealth } from "./features/runtime/RuntimeHealthProvider";
import { RuntimeHealthBanner, RuntimeHealthCompactIndicator, SemanticUnavailableNotice } from "./features/runtime/RuntimeHealthBanner";
import "./App.css";

type Theme = "dark" | "light";

interface AppProps {
  client?: MemeSortClient;
}

interface PageProps {
  title: string;
  eyebrow: string;
  children: ReactNode;
}

const primaryNavigation = [
  { to: "/", label: "Library", end: true },
  { to: "/duplicates", label: "Duplicates" },
];

const settingsNavigation = [{ to: "/settings", label: "Settings" }];

function Page({ title, eyebrow, children }: PageProps) {
  return (
    <main className="page" aria-labelledby="page-title">
      <div className="page-heading">
        <p className="eyebrow">{eyebrow}</p>
        <h1 id="page-title">{title}</h1>
      </div>
      {children}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metric">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function LibraryPage({ state, client }: { state: AppState; client: MemeSortClient }) {
  // Ticket 07: inspector target is URL-backed (`asset=<asset-id>`) so the
  // waterfall stays mounted, back/forward restores it, and closing removes
  // only `asset` while preserving q/sort/media/status.
  const { assetId: selectedAssetId, setAssetId, clearAssetId } = useLibraryUrlState();
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;

  return (
    <Page title="Your library" eyebrow="MemeSort desktop">
      <LibraryShell
        toolbar={
          <>
            <div className="library-toolbar-row">
              <LibraryImportMenu client={client} />
            </div>
            <section className="metric-grid" aria-label="Library summary">
              <Metric label="Assets" value={state.library_status.total_assets} />
              <Metric label="Pending jobs" value={pendingJobs} />
              <Metric label="Runtime" value={`${state.runtime.backend_name} / ${state.runtime.device}`} />
              <Metric label="Worker" value={state.worker_loop.paused ? "Paused" : "Running"} />
            </section>
          </>
        }
        content={
          <AssetsWorkspace
            client={client}
            selectedAssetId={selectedAssetId}
            onSelectAsset={setAssetId}
            onCloseDetail={clearAssetId}
          />
        }
      />
    </Page>
  );
}

function SettingsRoute() {
  return (
    <Page title="Settings" eyebrow="Configuration">
      <SettingsPage />
    </Page>
  );
}

function SetupPage({ state, client, onStateChanged }: { state: AppState; client: MemeSortClient; onStateChanged: () => void }) {
  const importBatch = useImportBatch();
  const startBatch = importBatch.startBatch;
  const health = useRuntimeHealth();
  const detail = state.setup_state.runtime_readiness?.ready_detail;
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const importTask = state.import_task;
  const importRunning = importTask?.running ?? false;
  // Current-session authorization (ticket 14) is authoritative for indexing.
  // Persisted/AppState health (`health_check_ok`) is informational only and
  // never authorizes indexing on its own.
  const canStartIndexing = health.isAuthorized;
  const healthResult = health.result;

  const runImportAction = async (action: () => Promise<unknown>, successMessage: string) => {
    setIsWorking(true);
    setFeedback(null);
    try {
      await action();
      setFeedback(successMessage);
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not complete the import action."));
    } finally {
      setIsWorking(false);
    }
  };

  const importStatus = (() => {
    if (importRunning) {
      if (importTask.paused) return "Import paused. Resume to continue with the next file.";
      if (importTask.pause_requested) return "Pausing import after the current file finishes safely.";
      return `Importing ${importTask.source_folder ?? "folder"}. Pause takes effect between files.`;
    }
    if (importTask?.status === "completed") {
      return `Import completed: ${importTask.result?.new_assets ?? 0} new Asset(s), ${importTask.result?.duplicate_assets ?? 0} duplicate(s).`;
    }
    if (importTask?.status === "failed" || importTask?.status === "cancelled") {
      return `Import ${importTask.status}: ${importTask.error?.detail ?? "unknown error"}`;
    }
    return "Choose a folder with the native dialog to begin an import.";
  })();

  const runHealthCheck = async () => {
    setIsWorking(true);
    setFeedback(null);
    try {
      const snapshot = await health.retry();
      const result = snapshot.result;
      setFeedback(
        snapshot.status === "healthy" && result
          ? `Runtime health check passed on ${result.device}.`
          : (snapshot.error ?? result?.error ?? "Runtime health check failed."),
      );
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not run the Vulkan health check."));
    } finally {
      setIsWorking(false);
    }
  };

  return (
    <Page title="Setup & runtime" eyebrow="Pinned Runtime">
      <section className="surface setup-card">
        <h2>Library Root</h2>
        <p className="mono">{state.library_root}</p>
        <p>
          MemeSort uses the manifest-pinned llama.cpp Vulkan0 runtime. Runtime and model selection are not configurable from the desktop app.
        </p>
      </section>
      <section className="surface import-card" aria-labelledby="runtime-descriptor-title">
        <h2 id="runtime-descriptor-title">Runtime Descriptor</h2>
        <p>{state.runtime.model_label ?? "Manifest-pinned model"} · {state.runtime.output_dimension ?? "unknown"}d · {state.runtime.storage_dtype ?? "unknown"}</p>
        <p>{state.runtime.backend_name} / {state.runtime.device}; this descriptor is read-only.</p>
      </section>
      {health.status === "checking" || health.status === "idle" ? (
        <section className="notice" role="status" aria-label="Runtime health">
          <strong>Preparing search…</strong>
          <span>Running one automatic Runtime health check for this app session. Browsing and import remain usable.</span>
        </section>
      ) : health.isAuthorized ? (
        <section className="notice notice-success" role="status">
          <strong>Runtime ready in this app session.</strong>
          <span>{detail ?? "The current health check authorizes indexing."}</span>
        </section>
      ) : (
        <>
          <RuntimeNotReady detail={health.error ?? detail} />
          <p>Run the external setup script to install the pinned runtime; this app does not install the Runtime. Library browsing and import still work.</p>
        </>
      )}
      <section className="surface import-card" aria-labelledby="health-check-title">
        <h2 id="health-check-title">Vulkan health check</h2>
        <p>Checks the manifest-pinned llama.cpp Vulkan0 runtime in this application session. Persisted health is informational only; only this session&apos;s check authorizes indexing.</p>
        <div className="import-actions">
          <button className="button button-secondary" type="button" disabled={isWorking || health.status === "checking"} onClick={() => void runHealthCheck()}>
            {health.isBlocked ? "Retry health check" : "Run Vulkan health check"}
          </button>
        </div>
        {healthResult ? <ul className="detail-list">{healthResult.diagnostic_steps.map((step) => <li key={`${step.step}-${step.status}`}><strong>{step.step} · {step.status} · {step.detail}</strong></li>)}</ul> : null}
      </section>
      <section className="surface import-card" aria-labelledby="setup-checklist-title">
        <h2 id="setup-checklist-title">Setup checklist</h2>
        {(state.setup_state.checklist?.length ?? 0) ? <ul className="detail-list">{state.setup_state.checklist!.map((item) => <li key={item.id}><strong>{item.done ? "Done" : "Next"} · {item.label} · {item.detail}</strong></li>)}</ul> : <p>Setup progress is not available yet.</p>}
      </section>
      <section className="surface import-card" aria-labelledby="import-title">
        <h2 id="import-title">Import Assets</h2>
        <p>Choose a source folder with the native dialog. MemeSort creates durable Library Copies; source paths remain import metadata.</p>
        <p className="mono">{selectedFolder ?? "No source folder selected"}</p>
        <div className="import-actions">
          <button
            className="button button-secondary"
            type="button"
            disabled={isWorking || importRunning}
            onClick={() => void runImportAction(async () => {
              const result = await client.chooseImportFolder();
              setSelectedFolder(result.selected_path);
            }, "Folder selection updated.")}
          >
            Choose import folder
          </button>
          <button
            className="button"
            type="button"
            disabled={isWorking || importRunning || !selectedFolder}
            onClick={() => void runImportAction(
              () => startBatch(() => client.startImport()),
              "Import started in the background.",
            )}
          >
            Import folder
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={isWorking || importRunning || !selectedFolder || !canStartIndexing}
            onClick={() => void runImportAction(
              () => startBatch(() => client.startImportAndIndex()),
              "Import and indexing started in the background.",
            )}
          >
            Import and index
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={isWorking || !importRunning || importTask.pause_requested}
            onClick={() => void runImportAction(client.pauseImport, "Pause requested. The current file will finish safely.")}
          >
            Pause import
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={isWorking || !importRunning || !importTask.pause_requested}
            onClick={() => void runImportAction(client.resumeImport, "Import resumed.")}
          >
            Resume import
          </button>
        </div>
        <p role="status">{feedback ?? importStatus}</p>
        {!canStartIndexing ? <p>Indexing remains unavailable until this app session has an authorized Pinned Runtime.</p> : null}
      </section>
    </Page>
  );
}

function SearchLandingPage() {
  return (
    <Page title="Search MemeSort" eyebrow="Search">
      <section className="surface search-landing-intro">
        <h2>Choose a Search Request</h2>
        <p>Start with text, an image, or an Asset already in your Library.</p>
      </section>
      <section className="search-options" aria-label="Search options">
        <Link className="search-option" to="/search/text">
          <h2>Text search</h2>
          <p>Describe a reaction, meme, or visual idea to find matching Assets.</p>
        </Link>
        <Link className="search-option" to="/search/image">
          <h2>Image search</h2>
          <p>Choose an image with the native dialog to find related Assets.</p>
        </Link>
        <Link className="search-option" to="/search/similar">
          <h2>Find similar Assets</h2>
          <p>Choose an Indexed Asset from your Library to retrieve related Assets.</p>
        </Link>
      </section>
      <section className="surface search-landing-help">
        <h2>No Indexed Assets yet?</h2>
        <p>Only Indexed Assets participate in semantic retrieval.</p>
        <p><Link to="/setup">Set up and index Assets</Link> to make them available for search.</p>
      </section>
    </Page>
  );
}

function TextSearchPage({ client }: { client: MemeSortClient }) {
  const health = useRuntimeHealth();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchAsset[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRequestId = useRef<string | null>(null);
  const semanticBlocked = health.isBlocked;

  const cancelCurrentSearch = useCallback(() => {
    const requestId = activeRequestId.current;
    if (!requestId) return;
    activeRequestId.current = null;
    void client.cancelSearch(requestId);
  }, [client]);

  useEffect(() => () => cancelCurrentSearch(), [cancelCurrentSearch]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const searchQuery = query.trim();
    if (!searchQuery) return;
    cancelCurrentSearch();
    const requestId = crypto.randomUUID();
    activeRequestId.current = requestId;
    setIsSearching(true);
    setError(null);
    try {
      const result = await client.searchText(searchQuery, requestId);
      if (activeRequestId.current === requestId) setResults(result.results);
    } catch (requestError) {
      if (activeRequestId.current === requestId) {
        setError(tauriErrorDetail(requestError, "MemeSort could not complete this Search Request."));
      }
    } finally {
      if (activeRequestId.current === requestId) {
        activeRequestId.current = null;
        setIsSearching(false);
      }
    }
  };

  return (
    <Page title="Text search" eyebrow="Search Request">
      <SemanticUnavailableNotice />
      <section className="surface search-panel">
        <form className="search-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="text-search-query">Search text</label>
          <div>
            <input id="text-search-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Describe a reaction or meme" />
            <button className="button" type="submit" disabled={isSearching || !query.trim() || semanticBlocked}>Search</button>
          </div>
        </form>
        <p>Each request is scoped to this page and is cancelled when you replace it or leave the page.</p>
        {semanticBlocked ? <p>Semantic search is unavailable until the current session passes the Runtime health check. Library browsing and import still work.</p> : null}
      </section>
      {isSearching ? <p aria-live="polite">Searching the Active Index Recipe…</p> : null}
      {error ? <section className="notice notice-warning" role="alert"><strong>Search unavailable</strong><span>{error}</span></section> : null}
      {results ? (
        results.length ? <section className="search-results" aria-label="Text search results">
          {results.map((result) => {
            const preview = mediaUrl(result.thumbnail_url) ?? mediaUrl(result.library_url);
            return <article className="search-result" key={result.asset_id}>
              {preview ? <img src={preview} alt="" /> : <div className="media-placeholder" aria-hidden="true" />}
              <div><strong>{result.library_path}</strong><p>{result.match_sources.join(" + ")} match · score {result.score.toFixed(3)}</p>{result.ocr_snippet ? <p>{result.ocr_snippet}</p> : null}</div>
            </article>;
          })}
        </section> : <EmptyState title="No matches yet" detail="Try a different description, or index more Assets with the Active Index Recipe." />
      ) : null}
    </Page>
  );
}

function ImageSearchPage({ client }: { client: MemeSortClient }) {
  const health = useRuntimeHealth();
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [results, setResults] = useState<SearchAsset[] | null>(null);
  const [isSelecting, setIsSelecting] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRequestId = useRef<string | null>(null);
  const semanticBlocked = health.isBlocked;

  const cancelCurrentSearch = useCallback(() => {
    const requestId = activeRequestId.current;
    if (!requestId) return;
    activeRequestId.current = null;
    void client.cancelSearch(requestId);
  }, [client]);

  useEffect(() => () => cancelCurrentSearch(), [cancelCurrentSearch]);

  const chooseImage = async () => {
    setIsSelecting(true);
    setError(null);
    try {
      const selection = await client.chooseSearchImage();
      setSelectedImage(selection.selected_path);
    } catch (selectionError) {
      setError(tauriErrorDetail(selectionError, "MemeSort could not choose an image."));
    } finally {
      setIsSelecting(false);
    }
  };

  const search = async () => {
    if (!selectedImage) return;
    cancelCurrentSearch();
    const requestId = crypto.randomUUID();
    activeRequestId.current = requestId;
    setIsSearching(true);
    setError(null);
    try {
      const result = await client.searchImage(requestId);
      if (activeRequestId.current === requestId) setResults(result.results);
    } catch (requestError) {
      if (activeRequestId.current === requestId) {
        setError(tauriErrorDetail(requestError, "MemeSort could not complete this Search Request."));
      }
    } finally {
      if (activeRequestId.current === requestId) {
        activeRequestId.current = null;
        setIsSearching(false);
      }
    }
  };

  return (
    <Page title="Image search" eyebrow="Search Request">
      <SemanticUnavailableNotice />
      <section className="surface search-panel">
        <h2>Search by image</h2>
        <p>Choose an image with the native dialog. Its filesystem path stays outside the WebView.</p>
        <p className="mono">{selectedImage ?? "No image selected"}</p>
        <div className="import-actions">
          <button className="button button-secondary" type="button" disabled={isSelecting || isSearching} onClick={() => void chooseImage()}>
            Choose image
          </button>
          <button className="button" type="button" disabled={isSelecting || isSearching || !selectedImage || semanticBlocked} onClick={() => void search()}>
            Search image
          </button>
        </div>
        <p>Each request is scoped to this page and is cancelled when you replace it or leave the page.</p>
        {semanticBlocked ? <p>Semantic search is unavailable until the current session passes the Runtime health check. Library browsing and import still work.</p> : null}
      </section>
      {isSearching ? <p aria-live="polite">Searching the Active Index Recipe…</p> : null}
      {error ? <section className="notice notice-warning" role="alert"><strong>Search unavailable</strong><span>{error}</span></section> : null}
      {results ? (
        results.length ? <section className="search-results" aria-label="Image search results" role="region">
          {results.map((result) => {
            const preview = mediaUrl(result.thumbnail_url) ?? mediaUrl(result.library_url);
            return <article className="search-result" key={result.asset_id}>
              {preview ? <img src={preview} alt="" /> : <div className="media-placeholder" aria-hidden="true" />}
              <div><strong>{result.library_path}</strong><p>{result.match_sources.join(" + ")} match · score {result.score.toFixed(3)}</p>{result.ocr_snippet ? <p>{result.ocr_snippet}</p> : null}</div>
            </article>;
          })}
        </section> : <EmptyState title="No matches yet" detail="Choose another image, or index more Assets with the Active Index Recipe." />
      ) : null}
    </Page>
  );
}

function SimilarSearchPage({ client }: { client: MemeSortClient }) {
  const health = useRuntimeHealth();
  const [assetId, setAssetId] = useState("");
  const [results, setResults] = useState<SearchAsset[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assetsQuery = useQuery({
    queryKey: ["similar-search-assets"],
    queryFn: () => client.getAssets(),
  });
  const indexedAssets = assetsQuery.data?.assets.filter((asset) => asset.status === "indexed") ?? [];
  const semanticBlocked = health.isBlocked;

  const search = async () => {
    if (!assetId) return;
    setIsSearching(true);
    setError(null);
    try {
      const result = await client.findSimilar(assetId);
      setResults(result.results);
    } catch (requestError) {
      setError(tauriErrorDetail(requestError, "MemeSort could not find similar Assets."));
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <Page title="Find similar Assets" eyebrow="Active Index Recipe">
      <SemanticUnavailableNotice />
      <section className="surface search-panel">
        <h2>Asset to Assets</h2>
        <p>Only Indexed Assets participate in semantic retrieval with the Active Index Recipe.</p>
        <label htmlFor="similar-asset">Indexed Asset</label>
        <div className="search-form">
          <select id="similar-asset" value={assetId} disabled={assetsQuery.isPending || isSearching} onChange={(event) => setAssetId(event.target.value)}>
            <option value="">Choose an Indexed Asset</option>
            {indexedAssets.map((asset) => <option key={asset.asset_id} value={asset.asset_id}>{asset.library_path}</option>)}
          </select>
          <button className="button" type="button" disabled={assetsQuery.isPending || isSearching || !assetId || semanticBlocked} onClick={() => void search()}>
            Find similar
          </button>
        </div>
        {semanticBlocked ? <p>Semantic search is unavailable until the current session passes the Runtime health check. Library browsing and import still work.</p> : null}
        {assetsQuery.isError ? <p role="alert">{tauriErrorDetail(assetsQuery.error, "MemeSort could not load Indexed Assets.")}</p> : null}
        {!assetsQuery.isPending && !assetsQuery.isError && !indexedAssets.length ? <p>No Indexed Assets are available yet.</p> : null}
      </section>
      {isSearching ? <p aria-live="polite">Finding similar Assets in the Active Index Recipe…</p> : null}
      {error ? <section className="notice notice-warning" role="alert"><strong>Search unavailable</strong><span>{error}</span></section> : null}
      {results ? (
        results.length ? <section className="search-results" aria-label="Similar Asset results" role="region">
          {results.map((result) => {
            const preview = mediaUrl(result.thumbnail_url) ?? mediaUrl(result.library_url);
            return <article className="search-result" key={result.asset_id}>
              {preview ? <img src={preview} alt="" /> : <div className="media-placeholder" aria-hidden="true" />}
              <div><strong>{result.library_path}</strong><p>{result.match_sources.join(" + ")} match · score {result.score.toFixed(3)}</p>{result.ocr_snippet ? <p>{result.ocr_snippet}</p> : null}</div>
            </article>;
          })}
        </section> : <EmptyState title="No similar Assets yet" detail="Index more Assets with the Active Index Recipe to expand this search." />
      ) : null}
    </Page>
  );
}

function DuplicatesPage({ client }: { client: MemeSortClient }) {
  const health = useRuntimeHealth();
  const [threshold, setThreshold] = useState("0.92");
  const [pairs, setPairs] = useState<DuplicatePair[] | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const thresholdValue = Number(threshold);
  const isValidThreshold = Number.isFinite(thresholdValue) && thresholdValue >= 0 && thresholdValue <= 1;
  const semanticBlocked = health.isBlocked;

  const scan = async () => {
    if (!isValidThreshold) return;
    setIsScanning(true);
    setError(null);
    try {
      const result = await client.getDuplicates(thresholdValue);
      setPairs(result.pairs);
    } catch (requestError) {
      setError(tauriErrorDetail(requestError, "MemeSort could not scan for duplicate Assets."));
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <Page title="Duplicate assets" eyebrow="Library maintenance">
      {semanticBlocked ? <SemanticUnavailableNotice /> : null}
      <section className="surface search-panel">
        <h2>Duplicate review</h2>
        <p>Compare Indexed Asset pairs from the Active Index Recipe. GIF matches use the strongest frame-to-frame score.</p>
        <label htmlFor="duplicate-threshold">Duplicate threshold</label>
        <div className="search-form">
          <input id="duplicate-threshold" type="number" min="0" max="1" step="0.01" value={threshold} onChange={(event) => setThreshold(event.target.value)} />
          <button className="button" type="button" disabled={isScanning || !isValidThreshold || semanticBlocked} onClick={() => void scan()}>
            Scan duplicates
          </button>
        </div>
        {!isValidThreshold ? <p role="alert">Enter a threshold between 0 and 1.</p> : null}
        {semanticBlocked ? <p>Duplicate review is unavailable until the current session passes the Runtime health check. Library browsing and import still work.</p> : null}
      </section>
      {isScanning ? <p aria-live="polite">Scanning Indexed Assets for duplicate pairs…</p> : null}
      {error ? <section className="notice notice-warning" role="alert"><strong>Duplicate scan unavailable</strong><span>{error}</span></section> : null}
      {pairs ? (
        pairs.length ? <section className="duplicate-pairs" aria-label="Duplicate pairs" role="region">
          {pairs.map((pair) => (
            <article className="duplicate-pair" key={`${pair.asset_a_id}-${pair.asset_b_id}`}>
              <header><strong>Similarity score {pair.score.toFixed(3)}</strong><span>Threshold {thresholdValue.toFixed(2)}</span></header>
              <div className="duplicate-assets">
                <div>
                  {mediaUrl(pair.asset_a_thumbnail_url) ? <img src={mediaUrl(pair.asset_a_thumbnail_url)!} alt="" /> : <div className="media-placeholder" aria-hidden="true" />}
                  <strong>{pair.asset_a_path}</strong>{pair.asset_a_matched_source_ref ? <p>Matched frame: {pair.asset_a_matched_source_ref}</p> : null}
                </div>
                <div>
                  {mediaUrl(pair.asset_b_thumbnail_url) ? <img src={mediaUrl(pair.asset_b_thumbnail_url)!} alt="" /> : <div className="media-placeholder" aria-hidden="true" />}
                  <strong>{pair.asset_b_path}</strong>{pair.asset_b_matched_source_ref ? <p>Matched frame: {pair.asset_b_matched_source_ref}</p> : null}
                </div>
              </div>
            </article>
          ))}
        </section> : <EmptyState title="No duplicate pairs found" detail="Lower the threshold or index more Assets, then scan again." />
      ) : null}
    </Page>
  );
}

function StatusPage({ state, client, onStateChanged }: { state: AppState; client: MemeSortClient; onStateChanged: () => void }) {
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;
  const [isWorking, setIsWorking] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [selectedPendingJobIds, setSelectedPendingJobIds] = useState<Set<string>>(() => new Set());
  const [pendingJobDeleteConfirmation, setPendingJobDeleteConfirmation] = useState(false);
  const pendingJobsQuery = useQuery({ queryKey: ["pending-jobs"], queryFn: () => client.getPendingJobs() });

  const runWorkerAction = async (action: () => Promise<unknown>, successMessage: string) => {
    setIsWorking(true);
    setFeedback(null);
    try {
      await action();
      setFeedback(successMessage);
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not update the Worker Loop."));
    } finally {
      setIsWorking(false);
    }
  };

  const retryFailedJobs = async () => {
    setIsWorking(true);
    setFeedback(null);
    try {
      const result = await client.retryFailedJobs();
      setFeedback(`Retried ${result.retried_jobs} failed Job record(s); ${result.failed_jobs_remaining} remain failed.`);
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not retry failed Job records. Assets and generated files were not modified."));
    } finally {
      setIsWorking(false);
    }
  };

  const togglePendingJob = (jobId: string) => {
    setSelectedPendingJobIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  const deleteSelectedPendingJobs = async () => {
    const jobIds = [...selectedPendingJobIds];
    if (!jobIds.length) return;

    setIsWorking(true);
    setFeedback(null);
    try {
      const result = await client.deletePendingJobs(jobIds);
      setFeedback(`Deleted ${result.deleted_job_ids.length} Pending Job record(s); skipped ${result.skipped_job_ids.length}.`);
      setSelectedPendingJobIds(new Set());
      setPendingJobDeleteConfirmation(false);
      await pendingJobsQuery.refetch();
      onStateChanged();
    } catch (error) {
      setFeedback(tauriErrorDetail(error, "MemeSort could not delete the selected Pending Job records. Assets and generated files were not modified."));
      setPendingJobDeleteConfirmation(false);
    } finally {
      setIsWorking(false);
    }
  };

  return (
    <Page title="Application status" eyebrow="Diagnostics">
      <section className="surface status-list">
        <dl>
          <Metric label="Library Root" value={<span className="mono">{state.library_root}</span>} />
          <Metric label="Assets" value={state.library_status.total_assets} />
          <Metric label="Pending jobs" value={pendingJobs} />
          <Metric label="Worker loop" value={state.worker_loop.running ? (state.worker_loop.paused ? "Paused" : "Running") : "Stopped"} />
        </dl>
      </section>
      <section className="surface import-card" aria-labelledby="worker-loop-title">
        <h2 id="worker-loop-title">Worker Loop</h2>
        <p>Controls apply only to the background indexing loop. They do not cancel a running semantic inference call.</p>
        <div className="import-actions">
          <button className="button" type="button" disabled={isWorking || !state.worker_loop.paused} onClick={() => void runWorkerAction(client.resumeWorkerLoop, "Worker Loop resumed.")}>Resume worker</button>
          <button className="button button-secondary" type="button" disabled={isWorking || state.worker_loop.paused} onClick={() => void runWorkerAction(client.pauseWorkerLoop, "Worker Loop paused.")}>Pause worker</button>
          <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => void retryFailedJobs()}>Retry failed Jobs</button>
          <button className="button button-secondary" type="button" disabled={isWorking || !state.worker_loop.running} onClick={() => void runWorkerAction(client.triggerWorkerLoop, "Worker Loop tick requested.")}>Run one tick</button>
        </div>
        <p role="status">{feedback ?? (state.worker_loop.paused ? "Worker Loop is paused." : "Worker Loop is running.")}</p>
      </section>
      <section className="surface import-card" aria-labelledby="pending-jobs-title">
        <h2 id="pending-jobs-title">Pending Jobs</h2>
        <p>Delete only unclaimed queue records. Assets and generated files remain unchanged.</p>
        {pendingJobsQuery.isPending ? <p aria-live="polite">Loading Pending Jobs…</p> : null}
        {pendingJobsQuery.isError ? <section className="notice notice-warning" role="alert"><strong>Pending Jobs are unavailable</strong><span>The Library was not modified. Retry when the sidecar is available.</span><button className="button button-secondary" type="button" onClick={() => void pendingJobsQuery.refetch()}>Retry Pending Jobs</button></section> : null}
        {pendingJobsQuery.data ? (
          pendingJobsQuery.data.jobs.length ? <>
            <ul className="detail-list pending-job-list">
              {pendingJobsQuery.data.jobs.map((job) => <li key={job.job_id}>
                <label className="asset-select"><input type="checkbox" checked={selectedPendingJobIds.has(job.job_id)} onChange={() => togglePendingJob(job.job_id)} />Select Pending Job {job.type}</label>
                <span>{job.asset_path ?? "No Asset path"}{job.recipe_id ? ` · ${job.recipe_id}` : ""} · attempt {job.attempt_count}</span>
              </li>)}
            </ul>
            <div className="import-actions"><button className="button button-danger" type="button" disabled={isWorking || !selectedPendingJobIds.size} onClick={() => setPendingJobDeleteConfirmation(true)}>Delete selected Pending Jobs</button></div>
          </> : <p>No Pending Jobs are waiting to be claimed.</p>
        ) : null}
      </section>
      {pendingJobDeleteConfirmation ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={isWorking ? undefined : () => setPendingJobDeleteConfirmation(false)}>
          <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="pending-job-delete-title" onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">Confirm change</p>
            <h2 id="pending-job-delete-title">Delete {selectedPendingJobIds.size} Pending Job(s)?</h2>
            <p>This deletes only unclaimed queue records. Assets and generated files remain unchanged.</p>
            <div className="dialog-actions"><button className="button button-secondary" type="button" disabled={isWorking} onClick={() => setPendingJobDeleteConfirmation(false)}>Cancel</button><button className="button button-danger" type="button" autoFocus disabled={isWorking} onClick={() => void deleteSelectedPendingJobs()}>{isWorking ? "Working…" : "Delete Pending Jobs"}</button></div>
          </section>
        </div>
      ) : null}
      <section className="surface import-card" aria-labelledby="recent-jobs-title">
        <h2 id="recent-jobs-title">Recent Jobs</h2>
        <p>Latest queue records for diagnosing retries and failures.</p>
        {(state.library_status.recent_jobs?.length ?? 0) ? <ul className="detail-list">{state.library_status.recent_jobs!.map((job) => <li key={job.job_id}><strong>{job.type} · {job.status} · attempt {job.attempt_count}</strong><span>{job.asset_id ?? "no Asset"} · updated {job.updated_at}</span>{job.error_detail ? <span>{job.error_detail}</span> : null}</li>)}</ul> : <p>No Job records are available yet.</p>}
      </section>
      <section className="surface import-card" aria-labelledby="worker-events-title">
        <h2 id="worker-events-title">Worker events</h2>
        <p>In-memory Worker Loop events from this application session.</p>
        {(state.worker_loop.recent_events?.length ?? 0) ? <ul className="detail-list">{state.worker_loop.recent_events!.map((event, index) => <li key={`${event.event}-${event.timestamp}-${index}`}><strong>{event.event}</strong><span>timestamp {event.timestamp}</span><code>{JSON.stringify(event.payload)}</code></li>)}</ul> : <p>No Worker Loop events yet.</p>}
      </section>
      <section className="surface import-card" aria-labelledby="persisted-worker-log-title">
        <h2 id="persisted-worker-log-title">Persisted worker log</h2>
        <p>Recent Worker Loop events written to the Library log.</p>
        <button className="button button-secondary" type="button" disabled={isWorking} onClick={() => void runWorkerAction(client.openLogDirectory, "Opened Library log folder.")}>Open log folder</button>
        {state.worker_loop.event_log_path ? <p className="mono">{state.worker_loop.event_log_path}</p> : null}
        {(state.worker_loop.persisted_events?.length ?? 0) ? <ul className="detail-list">{state.worker_loop.persisted_events!.map((event, index) => <li key={`${event.event}-${event.timestamp}-${index}`}><strong>{event.event}</strong><span>timestamp {event.timestamp}</span><code>{JSON.stringify(event.payload)}</code></li>)}</ul> : <p>No persisted Worker events yet.</p>}
      </section>
    </Page>
  );
}

function HelpDialog({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <p className="eyebrow">Keyboard</p>
        <h2 id="help-title">MemeSort navigation</h2>
        <p>Use Tab to move through the navigation and controls. Press Escape to close this dialog.</p>
        <button className="button button-secondary" type="button" autoFocus onClick={onClose}>Close</button>
      </section>
    </div>
  );
}

function NotFoundPage() {
  const location = useLocation();
  return (
    <Page title="Page not found" eyebrow="Navigation">
      <EmptyState title={`No route exists for ${location.pathname}`} detail="Return to the library workspace to continue." action={<Link className="button" to="/">Open library</Link>} />
    </Page>
  );
}

function ApplicationRoutes({ state, client, onStateChanged }: { state: AppState; client: MemeSortClient; onStateChanged: () => void }) {
  return (
    <Routes>
      <Route path="/" element={<LibraryPage state={state} client={client} />} />
      <Route path="/duplicates" element={<DuplicatesPage client={client} />} />
      <Route path="/settings" element={<SettingsRoute />} />
      <Route path="/setup" element={<SetupPage state={state} client={client} onStateChanged={onStateChanged} />} />
      <Route path="/search" element={<SearchLandingPage />} />
      <Route path="/search/text" element={<TextSearchPage client={client} />} />
      <Route path="/search/image" element={<ImageSearchPage client={client} />} />
      <Route path="/search/similar" element={<SimilarSearchPage client={client} />} />
      <Route path="/status" element={<StatusPage state={state} client={client} onStateChanged={onStateChanged} />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

function AppShell({ client }: { client: MemeSortClient }) {
  const [theme, setTheme] = useState<Theme>("dark");
  const [showHelp, setShowHelp] = useState(false);
  const stateQuery = useQuery({
    queryKey: ["app-state"],
    queryFn: () => client.getAppState(),
    // Polling app-state must not start another automatic health check (ticket 14).
    refetchInterval: 5_000,
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <Link className="brand" to="/" aria-label="MemeSort library">
          <span className="brand-mark">M</span>
          <span>MemeSort</span>
        </Link>
        <nav aria-label="Primary">
          {primaryNavigation.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <nav aria-label="Settings">
            {settingsNavigation.map(({ to, label }) => (
              <NavLink key={to} to={to} className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}>
                {label}
              </NavLink>
            ))}
          </nav>
          <button className="text-button" type="button" onClick={() => setShowHelp(true)}>Keyboard help</button>
          <button className="text-button" type="button" onClick={() => setTheme((value) => value === "dark" ? "light" : "dark")}>
            Use {theme === "dark" ? "light" : "dark"} theme
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span className={stateQuery.isSuccess ? "connection connection-online" : "connection"}>
            {stateQuery.isSuccess ? "Connected to MemeSort" : "Connecting…"}
          </span>
          <RuntimeHealthCompactIndicator />
          <span className="topbar-detail">Authenticated desktop session</span>
        </header>
        <ImportBatchPanel />
        <RuntimeHealthBanner />
        {stateQuery.isPending ? <LoadingState /> : null}
        {stateQuery.isError ? <SidecarDisconnected onRetry={() => void stateQuery.refetch()} /> : null}
        {stateQuery.isSuccess ? <ApplicationRoutes state={stateQuery.data} client={client} onStateChanged={() => void stateQuery.refetch()} /> : null}
      </div>
      {showHelp ? <HelpDialog onClose={() => setShowHelp(false)} /> : null}
    </div>
  );
}

export function App({ client = tauriClient }: AppProps) {
  return (
    <RuntimeHealthProvider client={client}>
      <ImportBatchProvider client={client}>
        <AppShell client={client} />
      </ImportBatchProvider>
    </RuntimeHealthProvider>
  );
}

export default App;
