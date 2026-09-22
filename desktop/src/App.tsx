import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import type { AppState } from "./api/types";
import { EmptyState, LoadingState, SidecarDisconnected } from "./components/States";
import {
  isRestorableFocusTarget,
  scheduleFocusRestoration,
  supportsModalDialog,
  useDialogFallback,
  useEscapeSurface,
  useModalDialog,
} from "./components/useEscapeSurface";
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
import { TaskBar } from "./features/tasks/TaskBar";
import { RuntimeHealthProvider } from "./features/runtime/RuntimeHealthProvider";
import { useOptionalRuntimeHealth } from "./features/runtime/useRuntimeHealth";
import { useTheme } from "./features/theme/ThemeContext";
import { ThemeProvider } from "./features/theme/ThemeProvider";
import type { ThemePreference } from "./features/theme/theme";
import { TitleBar } from "./features/window/TitleBar";
import { useWindowControls } from "./features/window/window-controls";
import { tauriErrorDetail } from "./api/tauri-error";
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
    <main className={`page${className ? ` ${className}` : ""}`} tabIndex={-1} aria-labelledby="page-title">
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
  const selectedAssetIdRef = useRef(selectedAssetId);
  const clearAssetIdRef = useRef(clearAssetId);
  selectedAssetIdRef.current = selectedAssetId;
  clearAssetIdRef.current = clearAssetId;
  const previousSelectedAssetIdRef = useRef(selectedAssetId);
  const detailCloseHandledRef = useRef(false);
  const optionalHealth = useOptionalRuntimeHealth();
  // Persisted health is informational. Search and visual retrieval require a
  // successful check in this application session; browsing and import do not.
  const semanticBlocked = optionalHealth ? !optionalHealth.isAuthorized : false;
  const [imagePickerError, setImagePickerError] = useState<string | null>(null);
  const imagePickerRequestRef = useRef<string | null>(null);
  const invalidateImagePicker = useCallback(() => {
    imagePickerRequestRef.current = null;
    setIsChoosingImage(false);
    setImagePickerError(null);
  }, []);
  useEffect(() => {
    invalidateImagePicker();
    return () => {
      imagePickerRequestRef.current = null;
    };
  }, [invalidateImagePicker, q, resultMode, semanticBlocked]);

  const handleQueryChange = useCallback(
    (next: string) => {
      invalidateImagePicker();
      setQuery(next);
    },
    [invalidateImagePicker, setQuery],
  );

  const handleSemanticSubmit = useCallback(
    (submitQuery: string) => {
      const trimmed = submitQuery.trim();
      if (!trimmed || semanticBlocked) return;
      invalidateImagePicker();
      const requestId = submitSearch(trimmed);
      if (requestId) {
        setQuery(trimmed);
        setResultMode({ kind: "semantic", query: trimmed, requestId });
      }
    },
    [invalidateImagePicker, submitSearch, setQuery, setResultMode, semanticBlocked],
  );

  const handleSearchModeChange = useCallback(
    (mode: LibrarySearchMode, draft: string) => {
      invalidateImagePicker();
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
    [clearQuery, clearSearch, invalidateImagePicker, setQuery, setResultMode],
  );

  const handleImageSearch = useCallback(async () => {
    if (semanticBlocked || imagePickerRequestRef.current) return;
    const requestId = crypto.randomUUID();
    imagePickerRequestRef.current = requestId;
    setImagePickerError(null);
    setIsChoosingImage(true);
    try {
      const selection = await client.chooseSearchImage(requestId);
      if (imagePickerRequestRef.current !== requestId) return;
      if (selection.request_id !== requestId) return;
      // Picker cancel (`selected_path: null`) leaves the current
      // Library/result state unchanged: no submit, no mode change.
      if (!selection.selected_path) return;
      const nextSelection = {
        id: requestId,
        label: selectedImageLabel(selection.selected_path),
        available: true,
      };
      const submitted = submitImageSearch(requestId, nextSelection.label);
      if (submitted) {
        setImagePickerError(null);
        setImageSelection(nextSelection);
        setResultMode({ kind: "image", selectionId: nextSelection.id });
      }
    } catch (error) {
      if (imagePickerRequestRef.current === requestId) {
        setImagePickerError(
          tauriErrorDetail(
            error,
            "MemeSort could not open the native image picker. Your Library was not modified.",
          ),
        );
      }
    } finally {
      if (imagePickerRequestRef.current === requestId) {
        imagePickerRequestRef.current = null;
        setIsChoosingImage(false);
      }
    }
  }, [client, semanticBlocked, setResultMode, submitImageSearch]);

  const handleFindSimilar = useCallback(
    (assetId: string) => {
      const trimmed = assetId.trim();
      if (!trimmed || semanticBlocked) return;
      invalidateImagePicker();
      const internalId = submitSimilarSearch(trimmed);
      if (internalId) {
        // Both inspector and card entries share this mode shape so they
        // produce identical behavior for the same Asset ID.
        setResultMode({ kind: "similar", assetId: trimmed });
      }
    },
    [invalidateImagePicker, submitSimilarSearch, setResultMode, semanticBlocked],
  );

  const handleClearSearch = useCallback(() => {
    invalidateImagePicker();
    clearSearch();
    clearQuery();
    setImageSelection(null);
    setResultMode({ kind: "browse" });
  }, [clearSearch, clearQuery, invalidateImagePicker, setResultMode]);

  const currentSearch = search.current;
  const missingImageSelectionId = currentSearch.status === "error"
    && currentSearch.errorCode === "ImageSelectionUnavailable"
    && !currentSearch.retryable
    && currentSearch.request.kind === "image"
    ? currentSearch.request.selectionId
    : null;
  const imageSelectionAvailable = imageSelection?.available === true
    && imageSelection.id !== missingImageSelectionId;
  useEffect(() => {
    if (!missingImageSelectionId) return;
    setImageSelection((current) => (
      current?.id === missingImageSelectionId && current.available
        ? { ...current, available: false }
        : current
    ));
  }, [missingImageSelectionId]);
  const handleRetrySearch = useCallback(() => {
    if (semanticBlocked || currentSearch.status !== "error" || !currentSearch.request) return;
    if (currentSearch.request.kind === "image" && !imageSelectionAvailable) return;
    retrySearch();
  }, [currentSearch, imageSelectionAvailable, retrySearch, semanticBlocked]);

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
    const openerElement = openerAssetId ? opener?.element ?? null : null;
    scheduleFocusRestoration(() => {
      const active = document.activeElement;
      if (active instanceof HTMLElement && active !== document.body && active.isConnected) return;
      const currentOpener = isRestorableFocusTarget(openerElement ?? null) && openerElement !== document.body
        ? openerElement
        : openerAssetId
          ? Array.from(document.querySelectorAll<HTMLElement>(".asset-card")).find(
              (card) => card.dataset.assetId === openerAssetId,
            )?.querySelector<HTMLElement>(".asset-card-open") ?? null
          : null;
      if (isRestorableFocusTarget(currentOpener)) {
        currentOpener.focus({ preventScroll: true });
        return;
      }
      const fallback = document.querySelector<HTMLElement>(".library-content");
      if (isRestorableFocusTarget(fallback)) fallback.focus({ preventScroll: true });
    });
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

  const handleInspectorDeleted = useCallback((deletedAssetId: string) => {
    if (selectedAssetIdRef.current === deletedAssetId) clearAssetIdRef.current();
  }, []);

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
              imagePickerError={imagePickerError}
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
            onChooseImage={semanticBlocked ? undefined : () => void handleImageSearch()}
            imageQueryLabel={imageSelection?.label ?? null}
            imageSelectionAvailable={imageSelectionAvailable}
            onFindSimilar={semanticBlocked ? undefined : handleFindSimilar}
          />
        }
        inspector={
          selectedAssetId ? (
            <AssetInspector
              key={selectedAssetId}
              assetId={selectedAssetId}
              client={client}
              onClose={handleCloseDetail}
              onDeleted={handleInspectorDeleted}
              onFindSimilar={handleFindSimilar}
              findSimilarDisabled={semanticBlocked}
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
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  useModalDialog(dialogRef);
  useDialogFallback(dialogRef);
  useEscapeSurface(!supportsModalDialog(), onClose);

  return (
    <dialog
      ref={dialogRef}
      className="dialog"
      aria-labelledby="help-title"
      open={!supportsModalDialog()}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <p className="eyebrow">Keyboard</p>
      <h2 id="help-title">MemeSort navigation</h2>
      <p>Use Tab to move through the navigation and controls. Press Escape to close this dialog.</p>
      <button autoFocus className="button button-secondary" type="button" onClick={onClose}>Close</button>
    </dialog>
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
    scheduleFocusRestoration(() => {
      const active = document.activeElement;
      if (active instanceof HTMLElement && active !== document.body && active.isConnected) return;
      const trigger = helpTriggerRef.current;
      if (isRestorableFocusTarget(trigger)) trigger.focus({ preventScroll: true });
    });
  }, []);
  const windowControls = useWindowControls();
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
        <div className="workspace-main">
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
