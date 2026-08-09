import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import { tauriErrorDetail } from "./api/tauri-error";
import type { AppState } from "./api/types";
import { EmptyState, LoadingState, RuntimeNotReady, SidecarDisconnected } from "./components/States";
import { AssetsWorkspace } from "./features/assets/AssetsWorkspace";
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

const navigation = [
  { to: "/", label: "Library", end: true },
  { to: "/setup", label: "Setup" },
  { to: "/search", label: "Search" },
  { to: "/duplicates", label: "Duplicates" },
  { to: "/status", label: "Status" },
];

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

function Dashboard({ state, client }: { state: AppState; client: MemeSortClient }) {
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;

  return (
    <Page title="Your library" eyebrow="MemeSort desktop">
      <section className="metric-grid" aria-label="Library summary">
        <Metric label="Assets" value={state.library_status.total_assets} />
        <Metric label="Pending jobs" value={pendingJobs} />
        <Metric label="Runtime" value={`${state.runtime.backend_name} / ${state.runtime.device}`} />
        <Metric label="Worker" value={state.worker_loop.paused ? "Paused" : "Running"} />
      </section>
      <AssetsWorkspace
        client={client}
        selectedAssetId={selectedAssetId}
        onSelectAsset={setSelectedAssetId}
        onCloseDetail={() => setSelectedAssetId(null)}
      />
    </Page>
  );
}

function SetupPage({ state, client, onStateChanged }: { state: AppState; client: MemeSortClient; onStateChanged: () => void }) {
  const detail = state.setup_state.runtime_readiness?.ready_detail;
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const importTask = state.import_task;
  const importRunning = importTask?.running ?? false;
  const canStartIndexing = state.setup_state.health_check_ok;

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

  return (
    <Page title="Setup & runtime" eyebrow="Pinned Runtime">
      <section className="surface setup-card">
        <h2>Library Root</h2>
        <p className="mono">{state.library_root}</p>
        <p>
          MemeSort uses the manifest-pinned llama.cpp Vulkan0 runtime. Runtime and model selection are not configurable from the desktop app.
        </p>
      </section>
      {state.setup_state.health_check_ok ? (
        <section className="notice notice-success" role="status">
          <strong>Runtime ready in this app session.</strong>
          <span>{detail ?? "The current health check authorizes indexing."}</span>
        </section>
      ) : (
        <RuntimeNotReady detail={detail} />
      )}
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
            onClick={() => void runImportAction(client.startImport, "Import started in the background.")}
          >
            Import folder
          </button>
          <button
            className="button button-secondary"
            type="button"
            disabled={isWorking || importRunning || !selectedFolder || !canStartIndexing}
            onClick={() => void runImportAction(client.startImportAndIndex, "Import and indexing started in the background.")}
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

function SearchPage({ title, detail }: { title: string; detail: string }) {
  return (
    <Page title={title} eyebrow="Search">
      <EmptyState title="Search is being migrated" detail={detail} />
    </Page>
  );
}

function DuplicatesPage() {
  return (
    <Page title="Duplicate assets" eyebrow="Library maintenance">
      <EmptyState
        title="Duplicate scanning is being migrated"
        detail="This route will show Asset pairs and their scores. GIF comparisons use the maximum frame-to-frame score."
      />
    </Page>
  );
}

function StatusPage({ state }: { state: AppState }) {
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;
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
      <Route path="/" element={<Dashboard state={state} client={client} />} />
      <Route path="/setup" element={<SetupPage state={state} client={client} onStateChanged={onStateChanged} />} />
      <Route path="/search" element={<SearchPage title="Search MemeSort" detail="Text, image, and similar-Asset retrieval will arrive as separate cancellable Search Request slices." />} />
      <Route path="/search/text" element={<SearchPage title="Text search" detail="Text retrieval will use an independently cancellable Search Request." />} />
      <Route path="/search/image" element={<SearchPage title="Image search" detail="Image retrieval will use the native image picker and a scoped Search Request." />} />
      <Route path="/search/similar" element={<SearchPage title="Find similar Assets" detail="Choose an Asset from the library to find semantically similar Assets." />} />
      <Route path="/duplicates" element={<DuplicatesPage />} />
      <Route path="/status" element={<StatusPage state={state} />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export function App({ client = tauriClient }: AppProps) {
  const [theme, setTheme] = useState<Theme>("dark");
  const [showHelp, setShowHelp] = useState(false);
  const stateQuery = useQuery({
    queryKey: ["app-state"],
    queryFn: () => client.getAppState(),
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
        <nav>
          {navigation.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
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
          <span className="topbar-detail">Authenticated desktop session</span>
        </header>
        {stateQuery.isPending ? <LoadingState /> : null}
        {stateQuery.isError ? <SidecarDisconnected onRetry={() => void stateQuery.refetch()} /> : null}
        {stateQuery.isSuccess ? <ApplicationRoutes state={stateQuery.data} client={client} onStateChanged={() => void stateQuery.refetch()} /> : null}
      </div>
      {showHelp ? <HelpDialog onClose={() => setShowHelp(false)} /> : null}
    </div>
  );
}

export default App;
