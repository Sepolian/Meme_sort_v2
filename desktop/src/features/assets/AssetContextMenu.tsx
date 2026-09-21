import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import {
  invalidateFocusRestorations,
  isRestorableFocusTarget,
  scheduleFocusRestoration,
  useEscapeSurface,
} from "../../components/useEscapeSurface";

export interface AssetContextMenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
}

const MENU_MIN_WIDTH = 224;
const MENU_ITEM_HEIGHT = 40;

export function AssetContextMenu({
  x,
  y,
  menuLabel,
  items,
  onClose,
  opener,
}: {
  x: number;
  y: number;
  menuLabel: string;
  items: readonly AssetContextMenuItem[];
  onClose: () => void;
  opener?: HTMLElement | null;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef(opener ?? null);
  const focusGenerationRef = useRef(0);
  const frameCancelRef = useRef<(() => void) | null>(null);
  const restoreRequestedRef = useRef(false);

  useLayoutEffect(() => {
    // A newly opened menu owns the next focus frame and supersedes a menu or
    // panel that was just dismissed before its restoration frame ran.
    invalidateFocusRestorations();
  }, []);

  useEffect(() => {
    if (openerRef.current === (opener ?? null)) return;
    openerRef.current = opener ?? null;
    focusGenerationRef.current += 1;
    frameCancelRef.current?.();
    frameCancelRef.current = null;
    restoreRequestedRef.current = false;
  }, [opener]);

  useEffect(() => () => {
    // An expected menu selection/cancel keeps its scheduled frame alive long
    // enough to restore the opener after this portal unmounts. Other teardown
    // (route change, replacement, or owner change) cancels it.
    if (!restoreRequestedRef.current) {
      focusGenerationRef.current += 1;
      frameCancelRef.current?.();
      frameCancelRef.current = null;
    }
  }, []);

  const restoreFocus = useCallback(() => {
    restoreRequestedRef.current = true;
    const generation = ++focusGenerationRef.current;
    const openerForRestore = openerRef.current;
    const menu = menuRef.current;
    frameCancelRef.current?.();
    frameCancelRef.current = scheduleFocusRestoration(() => {
      frameCancelRef.current = null;
      if (generation !== focusGenerationRef.current) return;
      const active = document.activeElement;
      // Let an explicit focus move made after dismissal win over restoration.
      if (
        active instanceof HTMLElement &&
        active !== document.body &&
        (!menu || !menu.contains(active))
      ) {
        return;
      }
      if (isRestorableFocusTarget(openerForRestore) && openerForRestore !== document.body) {
        openerForRestore.focus({ preventScroll: true });
        return;
      }
      const fallback = document.querySelector<HTMLElement>(".library-content");
      if (isRestorableFocusTarget(fallback)) fallback.focus({ preventScroll: true });
    });
  }, []);

  const dismissMenu = useCallback((restore: boolean) => {
    if (!restore) {
      focusGenerationRef.current += 1;
      frameCancelRef.current?.();
      frameCancelRef.current = null;
      restoreRequestedRef.current = false;
    }
    onClose();
    if (restore) restoreFocus();
  }, [onClose, restoreFocus]);

  const dismissWithFocus = useCallback(() => dismissMenu(true), [dismissMenu]);
  const dismissWithoutFocus = useCallback(() => dismissMenu(false), [dismissMenu]);

  useEscapeSurface(true, dismissWithFocus);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      // A right-click elsewhere (e.g. opening another card's menu) precedes
      // its `contextmenu` event with a `pointerdown`, so capture-phase
      // tracking here also guarantees at most one open menu.
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target as Node | null)
      ) {
        dismissWithoutFocus();
      }
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [dismissWithoutFocus]);

  // Render into `body` so transformed ancestors (e.g. the inspector enter
  // animation) cannot offset the fixed cursor position.
  const viewportWidth =
    typeof window === "undefined" ? 1024 : (window.innerWidth ?? 1024);
  const viewportHeight =
    typeof window === "undefined" ? 768 : (window.innerHeight ?? 768);
  const left = Math.max(8, Math.min(x, viewportWidth - MENU_MIN_WIDTH - 8));
  const top = Math.max(
    8,
    Math.min(y, viewportHeight - items.length * MENU_ITEM_HEIGHT - 24),
  );

  return createPortal(
    <div
      ref={menuRef}
      className="asset-context-menu"
      role="menu"
      aria-label={menuLabel}
      style={{ left, top }}
    >
      {items.map((item, index) => (
        <button
          key={item.label}
          ref={index === 0 ? focusFirst : undefined}
          className="asset-context-menu-item"
          type="button"
          role="menuitem"
          disabled={item.disabled}
          onClick={() => {
            if (item.disabled) return;
            item.onSelect();
            dismissWithFocus();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}

function focusFirst(element: HTMLButtonElement | null): void {
  // Focus the primary action so keyboard users land on Copy immediately.
  // jsdom implements `focus()`; browsers move real focus here.
  element?.focus();
}
