import { useCallback, useRef } from "react";
import type { ReactNode } from "react";
import { useEscapeSurface } from "./useEscapeSurface";

export function ConfirmDialog({
  titleId,
  title,
  detail,
  confirmLabel,
  pending,
  onCancel,
  onConfirm,
  onEscape,
  onRestoreFocus,
}: {
  titleId: string;
  title: ReactNode;
  detail: ReactNode;
  confirmLabel: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  /** Optional alternate callback for Escape; normal cancellation remains the fallback. */
  onEscape?: () => void;
  /** Overrides the default opener restoration when the owner tracks its opener. */
  onRestoreFocus?: () => void;
}) {
  const openerRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" &&
    document.activeElement instanceof HTMLElement &&
    document.activeElement !== document.body
      ? document.activeElement
      : null,
  );
  const restoreFocus = useCallback(() => {
    window.requestAnimationFrame(() => {
      if (openerRef.current?.isConnected) {
        openerRef.current.focus({ preventScroll: true });
        return;
      }
      const underlyingInspector = document.querySelector<HTMLElement>(
        ".library-inspector .inspector-header button",
      );
      (underlyingInspector ?? document.querySelector<HTMLElement>(".library-content"))?.focus({
        preventScroll: true,
      });
    });
  }, []);
  const dismiss = useCallback(() => {
    if (pending) return;
    (onEscape ?? onCancel)();
    (onRestoreFocus ?? restoreFocus)();
  }, [onCancel, onEscape, onRestoreFocus, pending, restoreFocus]);

  useEscapeSurface(true, dismiss);

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={pending ? undefined : dismiss}>
      <section className="dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(event) => event.stopPropagation()}>
        <p className="eyebrow">Confirm change</p>
        <h2 id={titleId}>{title}</h2>
        <p>{detail}</p>
        <div className="dialog-actions">
          <button className="button button-secondary" type="button" disabled={pending} onClick={dismiss}>Cancel</button>
          <button className="button button-danger" type="button" autoFocus disabled={pending} onClick={onConfirm}>{pending ? "Working…" : confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}
