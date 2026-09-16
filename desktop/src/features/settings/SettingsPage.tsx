import type { MemeSortClient } from "../../api/tauri-client";
import type { AppState } from "../../api/types";
import { ThemeSettingsControl } from "../theme/ThemeSettingsControl";
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

function RuntimeDescriptorSection({ appState }: { appState: AppState }) {
  return (
    <>
      <p>MemeSort uses the manifest-pinned llama.cpp Vulkan0 runtime. Runtime selection is not configurable.</p>
      <section className="surface import-card" aria-labelledby="settings-runtime-descriptor-title">
        <h3 id="settings-runtime-descriptor-title">Runtime Descriptor</h3>
        <p>{appState.runtime.model_label ?? "Manifest-pinned model"} · {appState.runtime.output_dimension ?? "unknown"}d · {appState.runtime.storage_dtype ?? "unknown"}</p>
        <p>{appState.runtime.backend_name} / {appState.runtime.device}; this descriptor is read-only.</p>
      </section>
      <section className="surface import-card" aria-labelledby="settings-active-recipe-title">
        <h3 id="settings-active-recipe-title">Active Index Recipe</h3>
        <p>Active recipe: {appState.asset_summary?.active_recipe_label ?? "Unavailable"}</p>
        <p className="mono">Recipe ID: {appState.asset_summary?.active_recipe_id ?? "Unavailable"}</p>
        <p>This manifest-derived recipe is read-only and defines semantic indexing and retrieval compatibility.</p>
      </section>
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
 * Final destination for Appearance, Accepted Duplicate Pair reset, the
 * read-only Runtime descriptor, external installation guidance, and Advanced
 * Diagnostics. Current-session Runtime health and recovery live in Activity.
 */
export function SettingsPage({
  client,
  appState,
  onStateChanged,
}: {
  client: MemeSortClient;
  appState: AppState;
  onStateChanged: () => void;
}) {
  return (
    <>
      <SettingsSection id="settings-appearance" title="Appearance">
        <p>
          Theme preference is persisted as system, dark, or light and defaults
          to system. System follows Windows appearance without a reload.
        </p>
        <ThemeSettingsControl />
      </SettingsSection>
      <SettingsSection id="settings-accepted-pairs" title="Accepted Duplicate Pairs">
        <AcceptedPairsSection client={client} onStateChanged={onStateChanged} />
      </SettingsSection>
      <SettingsSection id="settings-runtime" title="Runtime">
        <RuntimeDescriptorSection appState={appState} />
      </SettingsSection>
      <SettingsSection id="settings-installation" title="Installation">
        <InstallationSection />
      </SettingsSection>
      <SettingsSection id="settings-diagnostics" title="Advanced Diagnostics">
        <AdvancedDiagnostics client={client} appState={appState} onStateChanged={onStateChanged} />
      </SettingsSection>
    </>
  );
}
