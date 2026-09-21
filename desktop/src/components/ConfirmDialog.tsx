import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";
import {
  invalidateFocusRestorations,
  isRestorableFocusTarget,
  scheduleFocusRestoration,
  useDialogFocus,
  useEscapeSurface,
} from "./useEscapeSurface";

type FocusOwnershipCheck = () => boolean;
type RestoreFocus = (canRestoreFocus: FocusOwnershipCheck) => void;

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
  onRestoreFocus?: (canRestoreFocus: FocusOwnershipCheck) => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null);
  const confirmRequestedRef = useRef(false);
  const restoreRequestedRef = useRef(false);
  const restorationGenerationRef = useRef(0);
  const restorationFrameCancelRef = useRef<(() => void) | null>(null);
  const dialogOwnerRef = useRef<HTMLElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" &&
    document.activeElement instanceof HTMLElement &&
    document.activeElement !== document.body
      ? document.activeElement
      : null,
  );
  const restoreFocus = useCallback((canRestoreFocus: FocusOwnershipCheck) => {
    const generation = ++restorationGenerationRef.current;
    restorationFrameCancelRef.current?.();
    restorationFrameCancelRef.current = scheduleFocusRestoration(() => {
      restorationFrameCancelRef.current = null;
      if (generation !== restorationGenerationRef.current || !canRestoreFocus()) return;
      if (isRestorableFocusTarget(openerRef.current)) {
        openerRef.current.focus({ preventScroll: true });
        return;
      }
      const underlyingInspector = document.querySelector<HTMLElement>(
        ".library-inspector .inspector-header button",
      );
      if (isRestorableFocusTarget(underlyingInspector)) {
        underlyingInspector.focus({ preventScroll: true });
        return;
      }
      const fallback = document.querySelector<HTMLElement>(".library-content, .page");
      if (isRestorableFocusTarget(fallback)) fallback.focus({ preventScroll: true });
    });
  }, []);
  const restoreFocusRef = useRef<RestoreFocus>(onRestoreFocus ?? restoreFocus);
  restoreFocusRef.current = onRestoreFocus ?? restoreFocus;
  const restoreOwnerRef = useRef(onRestoreFocus ?? null);
  useEffect(() => {
    const nextOwner = onRestoreFocus ?? null;
    if (restoreOwnerRef.current === nextOwner) return;
    restoreOwnerRef.current = nextOwner;
    restorationGenerationRef.current += 1;
    restorationFrameCancelRef.current?.();
    restorationFrameCancelRef.current = null;
  }, [onRestoreFocus]);
  const restoreFocusOnce = useCallback(() => {
    if (restoreRequestedRef.current) return;
    restoreRequestedRef.current = true;
    const owner = dialogOwnerRef.current;
    restoreFocusRef.current(() => {
      const active = document.activeElement;
      return active === document.body || active === owner || Boolean(owner?.contains(active));
    });
  }, []);
  // A newly mounted dialog supersedes any pending restoration from a prior
  // dialog or inspector surface before the next paint/animation frame.
  useLayoutEffect(() => {
    dialogOwnerRef.current = dialogRef.current;
    invalidateFocusRestorations();
  }, []);
  useEffect(() => () => {
    // Expected cancel/success cleanup keeps its one restoration frame. An
    // unrelated owner unmount invalidates stale work instead.
    if (!confirmRequestedRef.current && !restoreRequestedRef.current) {
      restorationGenerationRef.current += 1;
      restorationFrameCancelRef.current?.();
      restorationFrameCancelRef.current = null;
      invalidateFocusRestorations();
    }
  }, []);
  useEffect(() => () => {
    if (confirmRequestedRef.current) restoreFocusOnce();
  }, [restoreFocusOnce]);
  const dismiss = useCallback(() => {
    if (pending) return;
    (onEscape ?? onCancel)();
    restoreFocusOnce();
  }, [onCancel, onEscape, pending, restoreFocusOnce]);
  const confirm = useCallback(() => {
    if (pending) return;
    confirmRequestedRef.current = true;
    onConfirm();
  }, [onConfirm, pending]);

  useEscapeSurface(true, dismiss);
  useDialogFocus(dialogRef, confirmButtonRef);

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={pending ? undefined : dismiss}>
      <section
        ref={dialogRef}
        className="dialog confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <p className="eyebrow">Confirm change</p>
        <h2 id={titleId}>{title}</h2>
        <p>{detail}</p>
        <div className="dialog-actions">
          <button className="button button-secondary" type="button" disabled={pending} onClick={dismiss}>Cancel</button>
          <button ref={confirmButtonRef} className="button button-danger" type="button" disabled={pending} onClick={confirm}>{pending ? "Working…" : confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}
