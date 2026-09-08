import memesortIcon from "../../assets/memesort-icon.png";
import type { WindowControls } from "./window-controls";

interface TitleBarProps {
  controls: WindowControls;
}

export function TitleBar({ controls }: TitleBarProps) {
  const maximizeLabel = controls.isMaximized ? "Restore window" : "Maximize window";

  return (
    <header className="titlebar">
      <div
        className="titlebar-drag-region"
        aria-label="Window drag region"
        data-tauri-drag-region="deep"
      >
        <img
          className="titlebar-brand-mark"
          src={memesortIcon}
          alt=""
          width="22"
          height="22"
          draggable="false"
          aria-hidden="true"
        />
        <span className="titlebar-brand">MemeSort</span>
      </div>
      <div className="titlebar-controls" aria-label="Window controls">
        <button
          className="window-control"
          type="button"
          aria-label="Minimize window"
          disabled={!controls.isNative}
          onClick={() => void controls.minimize()}
        >
          <span aria-hidden="true">−</span>
        </button>
        <button
          className="window-control"
          type="button"
          aria-label={maximizeLabel}
          disabled={!controls.isNative}
          onClick={() => void controls.toggleMaximize()}
        >
          <span className={controls.isMaximized ? "window-restore-icon" : "window-maximize-icon"} aria-hidden="true" />
        </button>
        <button
          className="window-control window-control-close"
          type="button"
          aria-label="Close window"
          disabled={!controls.isNative}
          onClick={() => void controls.close()}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
    </header>
  );
}
