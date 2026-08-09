import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { tauriClient, type MemeSortClient } from "./api/tauri-client";
import type { AppState } from "./api/types";
import { EmptyState, LoadingState, RuntimeNotReady, SidecarDisconnected } from "./components/States";
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

function Dashboard({ state }: { state: AppState }) {
  const pendingJobs = state.library_status.job_counts.pending ?? state.pending_jobs.length;

  return (
    <Page title="Your library" eyebrow="MemeSort desktop">
      <section className="metric-grid" aria-label="Library summary">
        <Metric label="Assets" value={state.library_status.total_assets} />
        <Metric label="Pending jobs" value={pendingJobs} />
        <Metric label="Runtime" value={`${state.runtime.backend_name} / ${state.runtime.device}`} />
        <Metric label="Worker" value={state.worker_loop.paused ? "Paused" : "Running"} />
      </section>
      {state.library_status.total_assets === 0 ? (
        <EmptyState
          title="No Assets yet"
          detail="Import a folder from Setup to create managed Library Copies."
          action={<Link className="button" to="/setup">Open setup</Link>}
        />
      ) : (
        <section className="surface">
          <h2>Library workspace</h2>
          <p>
            The Asset wall and Asset detail move here in the next vertical slice. Pending Assets and Failed Assets will remain browseable before they are searchable.
          </p>
        </section>
      )}
    </Page>
  );
}

function SetupPage({ state }: { state: AppState }) {
  const detail = state.setup_state.runtime_readiness?.ready_detail;
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

function ApplicationRoutes({ state }: { state: AppState }) {
  return (
    <Routes>
      <Route path="/" element={<Dashboard state={state} />} />
      <Route path="/setup" element={<SetupPage state={state} />} />
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
        {stateQuery.isSuccess ? <ApplicationRoutes state={stateQuery.data} /> : null}
      </div>
      {showHelp ? <HelpDialog onClose={() => setShowHelp(false)} /> : null}
    </div>
  );
}

export default App;
