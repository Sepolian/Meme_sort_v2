import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation, useMatch } from "react-router-dom";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import type { AppState } from "./api/types";
import { EmptyState, LoadingState, SidecarDisconnected } from "./components/States";
import { useEscapeSurface } from "./components/useEscapeSurface";
import { AssetsWorkspace } from "./features/assets/AssetsWorkspace";
import { AssetInspector } from "./features/assets/AssetInspector";
import { DuplicatesPage } from "./features/duplicates/DuplicatesPage";
import { LibraryControls } from "./features/library/LibraryControls";
import { LibraryImportMenu } from "./features/library/LibraryImportMenu";
import { LibrarySearchBar, type LibrarySearchMode } from "./features/library/LibrarySearchBar";
import { LibraryShell } from "./features/library/LibraryShell";
import { useLibraryUrlState } from "./features/library/useLibraryUrlState";
import { useLibrarySearch } from "./features/library/useLibrarySearch";
import { SettingsPage } from "./features/settings/SettingsPage";
import { ImportBatchProvider } from "./features/import/ImportBatchProvider";
import { ImportBatchPanel } from "./features/import/ImportBatchPanel";
import { TaskBar } from "./features/tasks/TaskBar";
import { RuntimeHealthProvider } from "./features/runtime/RuntimeHealthProvider";
import { useOptionalRuntimeHealth, useRuntimeHealth } from "./features/runtime/useRuntimeHealth";
import { RuntimeHealthBanner, RuntimeHealthCompactIndicator } from "./features/runtime/RuntimeHealthBanner";
import { useTheme } from "./features/theme/ThemeContext";
import { ThemeProvider } from "./features/theme/ThemeProvider";
import type { ThemePreference } from "./features/theme/theme";
import { TitleBar } from "./features/window/TitleBar";
import { useWindowControls } from "./features/window/window-controls";
import "./App.css";

interface AppProps {
  client?: MemeSortClient;
}

interface PageProps {
  title: string;
  eyebrow: string;
  children: ReactNode;
  className?: string;
  headingActions?: ReactNode;
  headingMeta?: ReactNode;
}

const primaryNavigation = [
  { to: "/", label: "Library", end: true },
  { to: "/duplicates", label: "Duplicates" },
];

const settingsNavigation = [{ to: "/settings", label: "Settings" }];

function selectedImageLabel(path: string): string {
  const segments = path.split(/[\\/]/);
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index]?.trim() ?? "";
    if (segment) return segment;
  }
  return "selected image";
}

function Page({ title, eyebrow, children, className, headingActions, headingMeta }: PageProps) {
  return (
    <main className={`page${className ? ` ${className}` : ""}`} aria-labelledby="page-title">
      <div className={`page-heading${headingActions || headingMeta ? " page-heading-with-actions" : ""}`}>
        <div className="page-heading-copy">
          <p className="eyebrow">{eyebrow}</p>
          <h1 id="page-title">{title}</h1>
          {headingMeta}
        </div>
        {headingActions}
      </div>
      {children}
    </main>
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
  // The Library search bar keeps a By meaning draft transient until explicit
  // submit, while By filename commits `q` for instant local filtering. Search
  // requests remain UUID-scoped; cancellation calls `cancelSearch(previous)`
  // before new work, clear/unmount cancel, and only the latest request identity
  // may commit (best-effort latest-wins).
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
    search,
    submitSearch,
    submitImageSearch,
    submitSimilarSearch,
    retrySearch,
    clearSearch,
  } = useLibrarySearch({ client, resultMode });
  const [isChoosingImage, setIsChoosingImage] = useState(false);
  const [imageSelection, setImageSelection] = useState<{ id: string; label: string; available: boolean } | null>(null);
  const detailOpenerRef = useRef<{ assetId: string; element: HTMLElement } | null>(null);
  const previousSelectedAssetIdRef = useRef(selectedAssetId);
  const detailCloseHandledRef = useRef(false);
  const optionalHealth = useOptionalRuntimeHealth();
  const semanticBlocked = optionalHealth?.isBlocked ?? false;

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
        setQuery(trimmed);
        setResultMode({ kind: "semantic", query: trimmed, requestId });
      }
    },
    [submitSearch, setQuery, setResultMode, semanticBlocked],
  );

  const handleSearchModeChange = useCallback(
    (mode: LibrarySearchMode, draft: string) => {
      clearSearch();
      if (mode === "filename") {
        setQuery(draft);
        setResultMode(
          draft.trim() ? { kind: "local", query: draft } : { kind: "browse" },
        );
        return;
      }
      clearQuery();
      setResultMode({ kind: "browse" });
    },
    [clearQuery, clearSearch, setQuery, setResultMode],
  );

  const handleImageSearch = useCallback(async () => {
    if (semanticBlocked || isChoosingImage) return;
    setIsChoosingImage(true);
    try {
      const selection = await client.chooseSearchImage();
      // Picker cancel (`selected_path: null`) leaves the current
      // Library/result state unchanged: no submit, no mode change.
      if (!selection.selected_path) {
        if (resultMode.kind === "image") {
          setImageSelection((current) => current ? { ...current, available: false } : current);
        }
        return;
      }
      const nextSelection = {
        id: crypto.randomUUID(),
        label: selectedImageLabel(selection.selected_path),
        available: true,
      };
      const requestId = submitImageSearch(nextSelection.id, nextSelection.label);
      if (requestId) {
        setImageSelection(nextSelection);
        setResultMode({ kind: "image", selectionId: nextSelection.id });
      }
    } catch {
      // Picker/transport failure also leaves browsing usable without
      // inventing a result mode; the image hook error surfaces only for
      // `searchImage` failures after a successful pick.
    } finally {
      setIsChoosingImage(false);
    }
  }, [client, resultMode.kind, submitImageSearch, setResultMode, semanticBlocked, isChoosingImage]);

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
    setImageSelection(null);
    setResultMode({ kind: "browse" });
  }, [clearSearch, clearQuery, setResultMode]);

  const currentSearch = search.current;
  const handleRetrySearch = useCallback(() => {
    if (semanticBlocked || currentSearch.status !== "error" || !currentSearch.request) return;
    if (currentSearch.request.kind === "image" && imageSelection?.available !== true) return;
    retrySearch();
  }, [currentSearch, imageSelection?.available, retrySearch, semanticBlocked]);

  const handleSelectAsset = useCallback(
    (assetId: string) => {
      // The card/View button remains the active element after its click. Keep
      // that trigger so closing this non-modal inspector restores focus to it.
      detailOpenerRef.current =
        document.activeElement instanceof HTMLElement && document.activeElement !== document.body
          ? { assetId, element: document.activeElement }
          : null;
      setAssetId(assetId);
    },
    [setAssetId],
  );

  const restoreDetailFocus = useCallback((closingAssetId: string) => {
    const opener = detailOpenerRef.current;
    const openerAssetId = opener?.assetId === closingAssetId ? closingAssetId : null;
    const openerElement = openerAssetId ? opener?.element : null;
    const focusAvailableTarget = (retry: boolean) => {
      const currentOpener = openerElement && openerElement !== document.body && openerElement.isConnected
        ? openerElement
        : openerAssetId
          ? Array.from(document.querySelectorAll<HTMLElement>(".asset-card")).find(
              (card) => card.dataset.assetId === openerAssetId,
            )?.querySelector<HTMLElement>(".asset-card-open") ?? null
          : null;
      if (currentOpener) {
        currentOpener.focus({ preventScroll: true });
        return;
      }
      if (retry && openerAssetId) {
        window.requestAnimationFrame(() => focusAvailableTarget(false));
        return;
      }
      document.querySelector<HTMLElement>(".library-content")?.focus({ preventScroll: true });
    };
    window.requestAnimationFrame(() => focusAvailableTarget(true));
  }, []);

  const handleCloseDetail = useCallback(() => {
    const focusWasInInspector =
      document.activeElement instanceof HTMLElement &&
      document.activeElement.closest(".library-inspector") !== null;
    detailCloseHandledRef.current = true;
    clearAssetId();
    if (!focusWasInInspector) return;
    restoreDetailFocus(selectedAssetId ?? "");
  }, [clearAssetId, restoreDetailFocus, selectedAssetId]);

  useEffect(() => {
    const opener = detailOpenerRef.current;
    if (selectedAssetId !== null && opener && opener.assetId !== selectedAssetId) {
      detailOpenerRef.current = null;
    }
  }, [selectedAssetId]);

  useEffect(() => {
    const previousAssetId = previousSelectedAssetIdRef.current;
    const wasOpen = previousAssetId !== null;
    previousSelectedAssetIdRef.current = selectedAssetId;
    const closeWasHandled = detailCloseHandledRef.current;
    detailCloseHandledRef.current = false;
    if (!wasOpen || selectedAssetId !== null || closeWasHandled) return;
    restoreDetailFocus(previousAssetId);
  }, [restoreDetailFocus, selectedAssetId]);

  return (
    <Page
      className="page-library"
      title="Your library"
      eyebrow="MemeSort desktop"
      headingMeta={
        <span className="library-heading-count">
          <strong>{state.library_status.total_assets}</strong>{" "}
          <span>{state.library_status.total_assets === 1 ? "Asset" : "Assets"}</span>
        </span>
      }
      headingActions={<LibraryImportMenu client={client} />}
    >
      <LibraryShell
        toolbar={
          <>
            <LibrarySearchBar
              query={q}
              isSearching={search.current.status === "loading"}
              semanticBlocked={semanticBlocked}
              isChoosingImage={isChoosingImage}
              onQueryChange={handleQueryChange}
              onSubmit={handleSemanticSubmit}
              onClear={handleClearSearch}
              onModeChange={handleSearchModeChange}
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
              onClearFilters={clearFilters}
            />
          </>
        }
        content={
          <AssetsWorkspace
            client={client}
            selectedAssetId={selectedAssetId}
            onSelectAsset={handleSelectAsset}
            onCloseDetail={handleCloseDetail}
            sort={sort}
            media={media}
            status={status}
            density={density}
            onClearFilters={clearFilters}
            query={q}
            resultMode={resultMode}
            search={search}
            onClearSearch={handleClearSearch}
            onRetrySearch={semanticBlocked ? undefined : handleRetrySearch}
            onChooseImage={() => void handleImageSearch()}
            imageQueryLabel={imageSelection?.label ?? null}
            imageSelectionAvailable={imageSelection?.available ?? false}
            onFindSimilar={handleFindSimilar}
          />
        }
        inspector={
          selectedAssetId ? (
            <AssetInspector
              assetId={selectedAssetId}
              client={client}
              onClose={handleCloseDetail}
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
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEscapeSurface(true, onClose);

  useEffect(() => {
    closeButtonRef.current?.focus({ preventScroll: true });
  }, []);

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
        <button ref={closeButtonRef} className="button button-secondary" type="button" onClick={onClose}>Close</button>
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
    <div className="route-content">
      <Routes>
        <Route path="/" element={<LibraryPage state={state} client={client} />} />
        <Route path="/duplicates" element={<DuplicatesPage client={client} />} />
        <Route path="/settings" element={<SettingsRoute state={state} client={client} onStateChanged={onStateChanged} />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </div>
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
  const helpTriggerRef = useRef<HTMLButtonElement | null>(null);
  const closeHelp = useCallback(() => {
    setShowHelp(false);
    window.requestAnimationFrame(() => {
      helpTriggerRef.current?.focus({ preventScroll: true });
    });
  }, []);
  const windowControls = useWindowControls();
  const settingsMatch = useMatch("/settings");
  const health = useRuntimeHealth();
  const stateQuery = useQuery({
    queryKey: ["app-state"],
    queryFn: () => client.getAppState(),
    // Polling app-state must not start another automatic health check (ticket 14).
    refetchInterval: 5_000,
  });
  return (
    <div className="app-shell">
      <TitleBar controls={windowControls} />
      <aside className="sidebar" aria-label="Primary navigation">
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
          <button ref={helpTriggerRef} className="text-button" type="button" onClick={() => setShowHelp(true)}>Keyboard help</button>
          <ThemeSidebarControl />
        </div>
      </aside>
      <div className="workspace">
        <header
          className="topbar"
          data-visible={health.status === "checking" ? "true" : "false"}
          aria-hidden={health.status !== "checking"}
        >
          <RuntimeHealthCompactIndicator />
        </header>
        <div className="workspace-main">
          <div className="workspace-status">
            <ImportBatchPanel />
            {settingsMatch ? null : <RuntimeHealthBanner />}
          </div>
          {stateQuery.isPending ? <LoadingState /> : null}
          {stateQuery.isError ? <SidecarDisconnected onRetry={() => void stateQuery.refetch()} /> : null}
          {stateQuery.isSuccess ? <ApplicationRoutes state={stateQuery.data} client={client} onStateChanged={() => void stateQuery.refetch()} /> : null}
          <TaskBar appState={stateQuery.data ?? null} />
        </div>
      </div>
      {showHelp ? <HelpDialog onClose={closeHelp} /> : null}
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
