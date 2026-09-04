import type { ThemePreference } from "./theme";
import { useTheme } from "./ThemeContext";

const OPTIONS: ReadonlyArray<{
  value: ThemePreference;
  label: string;
  detail: string;
}> = [
  {
    value: "system",
    label: "System",
    detail: "Follows Windows appearance and updates without reload.",
  },
  { value: "dark", label: "Dark", detail: "Always use the dark appearance." },
  { value: "light", label: "Light", detail: "Always use the light appearance." },
];

/**
 * Persisted theme preference control (ticket 18).
 *
 * Rendered in Settings > Appearance. The sidebar hosts a compact mirror of
 * the same preference via `ThemeSidebarControl` in `App.tsx`; both read and
 * write through the single `ThemeProvider` so they can never diverge.
 */
export function ThemeSettingsControl() {
  const { preference, resolved, setPreference } = useTheme();

  return (
    <div className="theme-control">
      <div
        className="theme-options"
        role="radiogroup"
        aria-label="Theme preference"
      >
        {OPTIONS.map((option) => (
          <label
            key={option.value}
            className="theme-option"
            data-selected={preference === option.value ? "true" : "false"}
          >
            <input
              type="radio"
              name="theme-preference"
              value={option.value}
              checked={preference === option.value}
              onChange={() => setPreference(option.value)}
            />
            <span className="theme-option-label">{option.label}</span>
            <span className="theme-option-detail">{option.detail}</span>
          </label>
        ))}
      </div>
      <p className="theme-status" role="status">
        Preference {preference}; showing {resolved} appearance.
      </p>
    </div>
  );
}

export default ThemeSettingsControl;
