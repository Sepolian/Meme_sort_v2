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

/**
 * Settings route skeleton (ticket 06).
 *
 * Final destination for Appearance, Accepted Duplicate Pair reset, Runtime
 * descriptor/health, external installation guidance, and Advanced Diagnostics.
 * Full behavior lands in tickets 14, 15, and 18; legacy Setup/Status routes
 * remain usable until those replacements ship (ticket 19).
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
        <p>MemeSort uses the manifest-pinned llama.cpp Vulkan0 runtime. Runtime selection is not configurable.</p>
        <p>Runtime descriptor, current health, and Retry land here with tickets 14 and 15.</p>
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
