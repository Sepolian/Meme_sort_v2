import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import type { AppState } from "./api/types";
import { EmptyState, LoadingState, SidecarDisconnected } from "./components/States";
import { AssetsWorkspace } from "./features/assets/AssetsWorkspace";
import { AssetInspector } from "./features/assets/AssetInspector";
import { DuplicatesPage } from "./features/duplicates/DuplicatesPage";
import { LibraryControls } from "./features/library/LibraryControls";
import { LibraryImportMenu } from "./features/library/LibraryImportMenu";
import { LibrarySearchBar } from "./features/library/LibrarySearchBar";
import { LibraryShell } from "./features/library/LibraryShell";
import { useLibraryUrlState } from "./features/library/useLibraryUrlState";
import { useLibraryTextSearch } from "./features/library/useLibraryTextSearch";
import { SettingsPage } from "./features/settings/SettingsPage";
import { ImportBatchProvider } from "./features/import/ImportBatchProvider";
import { ImportBatchPanel } from "./features/import/ImportBatchPanel";
import { TaskBar } from "./features/tasks/TaskBar";
import { TopBarTaskEntry } from "./features/tasks/TopBarTaskEntry";
import { RuntimeHealthProvider } from "./features/runtime/RuntimeHealthProvider";
import { useOptionalRuntimeHealth } from "./features/runtime/useRuntimeHealth";
import { RuntimeHealthBanner, RuntimeHealthCompactIndicator } from "./features/runtime/RuntimeHealthBanner";
import { useTheme } from "./features/theme/ThemeContext";
import { ThemeProvider } from "./features/theme/ThemeProvider";
import type { ThemePreference } from "./features/theme/theme";
import "./App.css";

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
  // Ticket 08: sort/media/status/density flow only through ticket 07's
  // URL/preference contract; this page owns the single hook instance and
  // passes effective values plus setters to the toolbar controls and the
  // ordered workspace.
  // Ticket 10: the inspector renders in `LibraryShell`'s non-overlaying
  // `aside` so the waterfall stays mounted with scroll preserved. Clipboard
  // Copy / Copy original file / Delete call ticket 05 client methods with
  // Asset IDs only; Find Similar exposes the ticket 12 action point.
  // Ticket 11: single Library search bar with instant local filtering
  // (typing updates `q`) and explicit UUID-scoped semantic submit via
  // `searchText(query, requestId)`. Cancellation calls
  // `cancelSearch(previous)` before new work; clear/unmount cancel; only the
  // latest request identity may commit (best-effort latest-wins).
  // Ticket 12: image attachment (`chooseSearchImage` then
  // `searchImage(requestId)`) and Find Similar (`findSimilar(assetId)`) reuse
  // the same composed, cancellable waterfall. Image/similar state stays
  // transient (never in URL/storage); picker cancel leaves state unchanged.
  const {
    q,
    sort,
    media,
    status,
    density,
    assetId: selectedAssetId,
    resultMode,
    setQuery,
    clearQuery,
    setSort,
    setMedia,
    setStatus,
    setDensity,
    setAssetId,
    clearAssetId,
    clearFilters,
    setResultMode,
  } = useLibraryUrlState();
  const {
    rawResults: semanticRawResults,
    committedQuery: semanticCommittedQuery,
    imageRawResults,
    committedImageRequestId,
    similarRawResults,
    committedSimilarAssetId,
    isSearching,
    searchError,
    submitSearch,
    submitImageSearch,
    submitSimilarSearch,
    clearSearch,
  } = useLibraryTextSearch({ client });
  const [isChoosingImage, setIsChoosingImage] = useState(false);
  const optionalHealth = useOptionalRuntimeHealth();
  const semanticBlocked = optionalHealth?.isBlocked ?? false;
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;

  const handleQueryChange = useCallback(
    (next: string) => {
      setQuery(next);
    },
    [setQuery],
  );

  const handleSemanticSubmit = useCallback(
    (submitQuery: string) => {
      const trimmed = submitQuery.trim();
      if (!trimmed || semanticBlocked) return;
      const requestId = submitSearch(trimmed);
      if (requestId) {
        setResultMode({ kind: "semantic", query: trimmed, requestId });
      }
    },
    [submitSearch, setResultMode, semanticBlocked],
  );

  const handleImageSearch = useCallback(async () => {
    if (semanticBlocked || isChoosingImage) return;
    setIsChoosingImage(true);
    try {
      const selection = await client.chooseSearchImage();
      // Picker cancel (`selected_path: null`) leaves the current
      // Library/result state unchanged: no submit, no mode change.
      if (!selection.selected_path) return;
      const requestId = submitImageSearch();
      setResultMode({ kind: "image", selectionId: requestId });
    } catch {
      // Picker/transport failure also leaves browsing usable without
      // inventing a result mode; the image hook error surfaces only for
      // `searchImage` failures after a successful pick.
    } finally {
      setIsChoosingImage(false);
    }
  }, [client, submitImageSearch, setResultMode, semanticBlocked, isChoosingImage]);

  const handleFindSimilar = useCallback(
    (assetId: string) => {
      const trimmed = assetId.trim();
      if (!trimmed || semanticBlocked) return;
      const internalId = submitSimilarSearch(trimmed);
      if (internalId) {
        // Both inspector and card entries share this mode shape so they
        // produce identical behavior for the same Asset ID.
        setResultMode({ kind: "similar", assetId: trimmed });
      }
    },
    [submitSimilarSearch, setResultMode, semanticBlocked],
  );

  const handleClearSearch = useCallback(() => {
    clearSearch();
    clearQuery();
    setResultMode({ kind: "browse" });
  }, [clearSearch, clearQuery, setResultMode]);

  return (
    <Page title="Your library" eyebrow="MemeSort desktop">
      <LibraryShell
        toolbar={
          <>
            <div className="library-toolbar-row">
              <LibraryImportMenu client={client} />
            </div>
            <LibrarySearchBar
              query={q}
              isSearching={isSearching}
              semanticBlocked={semanticBlocked}
              isChoosingImage={isChoosingImage}
              onQueryChange={handleQueryChange}
              onSubmit={handleSemanticSubmit}
              onClear={handleClearSearch}
              onImageSearch={() => void handleImageSearch()}
            />
            <LibraryControls
              sort={sort}
              media={media}
              status={status}
              density={density}
              onSortChange={setSort}
              onMediaChange={setMedia}
              onStatusChange={setStatus}
              onDensityChange={setDensity}
            />
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
            sort={sort}
            media={media}
            status={status}
            density={density}
            onClearFilters={clearFilters}
            query={q}
            resultMode={resultMode}
            semanticRawResults={semanticRawResults}
            semanticQuery={semanticCommittedQuery}
            imageRawResults={imageRawResults}
            committedImageRequestId={committedImageRequestId}
            similarRawResults={similarRawResults}
            committedSimilarAssetId={committedSimilarAssetId}
            isSearching={isSearching}
            searchError={searchError}
            onClearSearch={handleClearSearch}
            onFindSimilar={handleFindSimilar}
          />
        }
        inspector={
          selectedAssetId ? (
            <AssetInspector
              assetId={selectedAssetId}
              client={client}
              onClose={clearAssetId}
              onFindSimilar={handleFindSimilar}
            />
          ) : undefined
        }
      />
    </Page>
  );
}

function SettingsRoute({ state, client, onStateChanged }: { state: AppState; client: MemeSortClient; onStateChanged: () => void }) {
  return (
    <Page title="Settings" eyebrow="Configuration">
      <SettingsPage client={client} appState={state} onStateChanged={onStateChanged} />
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
      <Route path="/settings" element={<SettingsRoute state={state} client={client} onStateChanged={onStateChanged} />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

function ThemeSidebarControl() {
  // Compact sidebar mirror of the Settings > Appearance preference. Both read
  // and write through the single ThemeProvider (ticket 18 sole ownership).
  const { preference, setPreference } = useTheme();
  return (
    <label className="sidebar-theme" htmlFor="sidebar-theme-select">
      <span>Theme</span>
      <select
        id="sidebar-theme-select"
        aria-label="Theme preference"
        value={preference}
        onChange={(event) => setPreference(event.target.value as ThemePreference)}
      >
        <option value="system">System</option>
        <option value="dark">Dark</option>
        <option value="light">Light</option>
      </select>
    </label>
  );
}

function AppShell({ client }: { client: MemeSortClient }) {
  const [showHelp, setShowHelp] = useState(false);
  const stateQuery = useQuery({
    queryKey: ["app-state"],
    queryFn: () => client.getAppState(),
    // Polling app-state must not start another automatic health check (ticket 14).
    refetchInterval: 5_000,
  });

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
          <ThemeSidebarControl />
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span className={stateQuery.isSuccess ? "connection connection-online" : "connection"}>
            {stateQuery.isSuccess ? "Connected to MemeSort" : "Connecting…"}
          </span>
          <RuntimeHealthCompactIndicator />
          <TopBarTaskEntry appState={stateQuery.data ?? null} />
          <span className="topbar-detail">Authenticated desktop session</span>
        </header>
        <ImportBatchPanel />
        <RuntimeHealthBanner />
        {stateQuery.isPending ? <LoadingState /> : null}
        {stateQuery.isError ? <SidecarDisconnected onRetry={() => void stateQuery.refetch()} /> : null}
        {stateQuery.isSuccess ? <ApplicationRoutes state={stateQuery.data} client={client} onStateChanged={() => void stateQuery.refetch()} /> : null}
        <TaskBar appState={stateQuery.data ?? null} />
      </div>
      {showHelp ? <HelpDialog onClose={() => setShowHelp(false)} /> : null}
    </div>
  );
}

export function App({ client = tauriClient }: AppProps) {
  return (
    <ThemeProvider>
      <RuntimeHealthProvider client={client}>
        <ImportBatchProvider client={client}>
          <AppShell client={client} />
        </ImportBatchProvider>
      </RuntimeHealthProvider>
    </ThemeProvider>
  );
}

export default App;
