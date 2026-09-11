import { useEffect } from "react";
import type { ReactNode } from "react";

export function ConfirmDialog({
  titleId,
  title,
  detail,
  confirmLabel,
  pending,
  onCancel,
  onConfirm,
  onEscape,
}: {
  titleId: string;
  title: ReactNode;
  detail: ReactNode;
  confirmLabel: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  /** Lets a dialog own Escape dismissal; other surfaces keep key handling in their parent. */
  onEscape?: () => void;
}) {
  useEffect(() => {
    if (!onEscape) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!pending) onEscape();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onEscape, pending]);
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={pending ? undefined : onCancel}>
      <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(event) => event.stopPropagation()}>
        <p className="eyebrow">Confirm change</p>
        <h2 id={titleId}>{title}</h2>
        <p>{detail}</p>
        <div className="dialog-actions">
          <button className="button button-secondary" type="button" disabled={pending} onClick={onCancel}>Cancel</button>
          <button className="button button-danger" type="button" autoFocus disabled={pending} onClick={onConfirm}>{pending ? "Working…" : confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}
