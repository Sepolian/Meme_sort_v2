import { useCallback, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import {
  isRestorableFocusTarget,
  scheduleFocusRestoration,
  supportsModalDialog,
  useDialogFallback,
  useEscapeSurface,
  useModalDialog,
} from "./useEscapeSurface";

export function ConfirmDialog({
  titleId,
  title,
  detail,
  confirmLabel,
  pending,
  onCancel,
  onConfirm,
  onEscape,
  restoreFocusTo,
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
  restoreFocusTo?: HTMLElement | null;
}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const dialogOwnerRef = useRef<HTMLDialogElement | null>(null);
  const restoreRequestedRef = useRef(false);
  const openerRef = useRef<HTMLElement | null>(restoreFocusTo ?? (
    typeof document !== "undefined"
    && document.activeElement instanceof HTMLElement
    && document.activeElement !== document.body
      ? document.activeElement
      : null
  ));

  useModalDialog(dialogRef);
  useDialogFallback(dialogRef);
  const dismiss = useCallback(() => {
    if (pending) return;
    restoreRequestedRef.current = true;
    (onEscape ?? onCancel)();
  }, [onCancel, onEscape, pending]);
  useEscapeSurface(!supportsModalDialog(), dismiss);

  useEffect(() => {
    dialogOwnerRef.current = dialogRef.current;
    const opener = openerRef.current;
    return () => {
      if (!restoreRequestedRef.current) return;
      const owner = dialogOwnerRef.current;
      const canRestoreFocus = () => {
        const active = document.activeElement;
        return active === document.body
          || active === owner
          || Boolean(owner?.contains(active));
      };
      scheduleFocusRestoration(() => {
        if (!canRestoreFocus()) return;
        const inspectorFallback = document.querySelector<HTMLElement>(
          ".library-inspector .inspector-header button",
        );
        // If confirming removed the opener's owning surface, that surface
        // owns the next focus target (for example, closing the inspector).
        if (
          opener
          && !opener.isConnected
          && opener.closest(".library-inspector")
          && !inspectorFallback
        ) return;
        if (isRestorableFocusTarget(opener)) {
          opener.focus({ preventScroll: true });
          return;
        }
        const fallback = inspectorFallback
          ?? document.querySelector<HTMLElement>(".library-content, .page");
        if (isRestorableFocusTarget(fallback)) fallback.focus({ preventScroll: true });
      });
    };
  }, []);

  const confirm = useCallback(() => {
    if (pending) return;
    restoreRequestedRef.current = true;
    onConfirm();
  }, [onConfirm, pending]);

  return (
    <dialog
      ref={dialogRef}
      className="dialog confirm-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      open={!supportsModalDialog()}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
    >
      <p className="eyebrow">Confirm change</p>
      <h2 id={titleId}>{title}</h2>
      <p>{detail}</p>
      <div className="dialog-actions">
        <button className="button button-secondary" type="button" disabled={pending} onClick={dismiss}>Cancel</button>
        <button autoFocus className="button button-danger" type="button" disabled={pending} onClick={confirm}>{pending ? "Working…" : confirmLabel}</button>
      </div>
    </dialog>
  );
}
