import { useState } from "react";
import type { MemeSortClient } from "../../api/tauri-client";
import type { AppState } from "../../api/types";
import { useRuntimeHealth } from "../runtime/RuntimeHealthProvider";
import { AdvancedDiagnostics } from "./AdvancedDiagnostics";
import { AcceptedPairsSection } from "./AcceptedPairsSection";

interface SettingsSectionProps {
  id: string;
  title: string;
  children: React.ReactNode;
}

function SettingsSection({ id, title, children }: SettingsSectionProps) {
  return (
    <section className="surface settings-section" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {children}
    </section>
  );
}

function RuntimeHealthSection({ appState }: { appState: AppState | null }) {
  const health = useRuntimeHealth();
  const [isRetrying, setIsRetrying] = useState(false);

  const onRetry = async () => {
    setIsRetrying(true);
    try {
      await health.retry();
    } finally {
      setIsRetrying(false);
    }
  };

  return (
    <>
      <p>MemeSort uses the manifest-pinned llama.cpp Vulkan0 runtime. Runtime selection is not configurable.</p>
      {appState ? (
        <section className="surface import-card" aria-labelledby="settings-runtime-descriptor-title">
          <h3 id="settings-runtime-descriptor-title">Runtime Descriptor</h3>
          <p>{appState.runtime.model_label ?? "Manifest-pinned model"} · {appState.runtime.output_dimension ?? "unknown"}d · {appState.runtime.storage_dtype ?? "unknown"}</p>
          <p>{appState.runtime.backend_name} / {appState.runtime.device}; this descriptor is read-only.</p>
        </section>
      ) : (
        <p>Runtime descriptor is unavailable until the Library state loads.</p>
      )}
      {health.status === "checking" || health.status === "idle" ? (
        <p role="status" aria-label="Runtime health">
          Preparing search…
        </p>
      ) : health.isAuthorized ? (
        <p role="status">
          Runtime ready in this app session{health.result ? ` on ${health.result.device}` : ""}. This session authorizes indexing and semantic search.
        </p>
      ) : (
        <section className="notice notice-warning" role="alert" aria-label="Runtime health failure">
          <strong>Semantic search and indexing are unavailable</strong>
          <span>{health.result?.error ?? health.error ?? "The current session health check failed."}</span>
          <span>Library browsing and import still work. Run the external setup script to install the pinned runtime; this app does not install the Runtime.</span>
        </section>
      )}
      {health.result?.diagnostic_steps?.length ? (
        <ul className="detail-list">
          {health.result.diagnostic_steps.map((step) => (
            <li key={`${step.step}-${step.status}`}>
              <strong>
                {step.step} · {step.status} · {step.detail}
              </strong>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="import-actions">
        <button className="button button-secondary" type="button" disabled={isRetrying || health.status === "checking"} onClick={() => void onRetry()}>
          {isRetrying ? "Retrying…" : "Retry health check"}
        </button>
      </div>
    </>
  );
}

function InstallationSection() {
  return (
    <>
      <p>
        Runtime installation remains the responsibility of the external pre-launch setup script.
        This app shows instructions and state but does not install the Runtime.
      </p>
      <p>For a repository checkout, run from the repository root:</p>
      <p className="mono">Set-ExecutionPolicy -Scope Process Bypass</p>
      <p className="mono">.\scripts\setup_windows_llama.ps1</p>
      <p>
        The script reads <span className="mono">runtime-manifest.json</span>, downloads and verifies the
        project-local uv tool, llama.cpp Vulkan archive, and GGUF model plus projector, then provisions
        the application and isolated CPU OCR environments.
      </p>
      <p>For the portable desktop package, install the runtime beside MemeSort.exe:</p>
      <p className="mono">Double-click setup_portable_runtime.bat</p>
      <p>
        The batch wrapper runs the packaged PowerShell script with a process-local execution-policy bypass.
        It verifies every Vulkan/GGUF artifact by size and SHA256 and writes only below MemeSortData.
      </p>
      <p>
        If the Runtime health check fails, rerun the setup script; do not replace an artifact manually.
        If activation or a GGUF hash check fails, rerun setup with network access. Library browsing and
        import remain usable while the Runtime is unavailable.
      </p>
    </>
  );
}

/**
 * Settings route (tickets 06, 14, 15).
 *
 * Final destination for Appearance, Accepted Duplicate Pair reset, Runtime
 * descriptor/health, external installation guidance, and Advanced Diagnostics.
 * Ticket 14 owns the current-session health display and Retry; ticket 15 owns
 * the full Runtime descriptor, external setup-script instructions, and the
 * Advanced Diagnostics parity surface. Legacy Setup/Status routes remain usable
 * until those replacements ship (ticket 19).
 */
export function SettingsPage({
  client,
  appState,
  onStateChanged,
}: {
  client?: MemeSortClient;
  appState?: AppState | null;
  onStateChanged?: () => void;
}) {
  const diagnosticsReady = client && appState && onStateChanged;
  return (
    <>
      <SettingsSection id="settings-appearance" title="Appearance">
        <p>Theme controls live here. Ticket 18 owns the persisted system | dark | light preference.</p>
        <p>For now, use the sidebar theme toggle during migration.</p>
      </SettingsSection>
      <SettingsSection id="settings-accepted-pairs" title="Accepted Duplicate Pairs">
        {client ? (
          <AcceptedPairsSection client={client} onStateChanged={onStateChanged} />
        ) : (
          <>
            <p>
              Accepted Duplicate Pair management lives here. Pairs are unordered, excluded from
              future duplicate review, and removed when either Asset is deleted.
            </p>
            <p>Diagnostics load with the Library state. If this message persists, return to Library and reopen Settings.</p>
          </>
        )}
      </SettingsSection>
      <SettingsSection id="settings-runtime" title="Runtime">
        <RuntimeHealthSection appState={appState ?? null} />
      </SettingsSection>
      <SettingsSection id="settings-installation" title="Installation">
        <InstallationSection />
      </SettingsSection>
      <SettingsSection id="settings-diagnostics" title="Advanced Diagnostics">
        {diagnosticsReady ? (
          <AdvancedDiagnostics client={client} appState={appState} onStateChanged={onStateChanged} />
        ) : (
          <>
            <p>Worker Loop pause, resume, and tick, failed-Job retry, Pending Job inspection, Recent Jobs, Worker events, and log-directory actions live here.</p>
            <p>Diagnostics load with the Library state. If this message persists, return to Library and reopen Settings.</p>
          </>
        )}
      </SettingsSection>
    </>
  );
}

export default SettingsPage;
