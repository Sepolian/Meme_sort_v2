import { useState } from "react";
import { useRuntimeHealth } from "../runtime/RuntimeHealthProvider";

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

function RuntimeHealthSection() {
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
          <span>Library browsing and import still work.</span>
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
      <p>Full Runtime descriptor and Advanced Diagnostics land with ticket 15.</p>
    </>
  );
}

/**
 * Settings route skeleton (ticket 06) with startup health lifecycle (ticket 14).
 *
 * Final destination for Appearance, Accepted Duplicate Pair reset, Runtime
 * descriptor/health, external installation guidance, and Advanced Diagnostics.
 * Ticket 14 owns the current-session health display and Retry; full behavior
 * lands in tickets 15 and 18; legacy Setup/Status routes remain usable until
 * those replacements ship (ticket 19).
 */
export function SettingsPage() {
  return (
    <>
      <SettingsSection id="settings-appearance" title="Appearance">
        <p>Theme controls live here. Ticket 18 owns the persisted system | dark | light preference.</p>
        <p>For now, use the sidebar theme toggle during migration.</p>
      </SettingsSection>
      <SettingsSection id="settings-accepted-pairs" title="Accepted Duplicate Pairs">
        <p>
          Accepted Duplicate Pair management lives here. Pairs are unordered, excluded from
          future duplicate review, and removed when either Asset is deleted.
        </p>
        <p>Clear-all and pair inspection land with tickets 02, 03, and 16.</p>
      </SettingsSection>
      <SettingsSection id="settings-runtime" title="Runtime">
        <RuntimeHealthSection />
      </SettingsSection>
      <SettingsSection id="settings-installation" title="Installation">
        <p>
          Runtime installation remains the responsibility of the external pre-launch setup script.
          This app shows instructions and state but does not install the Runtime.
        </p>
        <p>Full external installation guidance lands here with ticket 15.</p>
      </SettingsSection>
      <SettingsSection id="settings-diagnostics" title="Advanced Diagnostics">
        <p>Worker Loop pause, resume, and tick, failed-Job retry, Pending Job inspection, Recent Jobs, Worker events, and log-directory actions land here with ticket 15.</p>
        <p>For now, use Status during migration.</p>
      </SettingsSection>
    </>
  );
}

export default SettingsPage;
