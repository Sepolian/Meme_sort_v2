import type { ReactNode } from "react";

interface LibraryShellProps {
  toolbar: ReactNode;
  content: ReactNode;
  inspector?: ReactNode;
}

/**
 * Final Library workspace skeleton (ticket 06).
 *
 * Provides the three required zones without owning future behavior:
 * - toolbar: Library summary and (later) sort/filter/search/import controls.
 * - content: scrollable browsing area. Reuses AssetsWorkspace until later tickets replace it.
 * - inspector: optional non-overlaying right region. Rendered side-by-side when present,
 *   never as an overlay, so the waterfall stays mounted and keeps scroll position.
 */
export function LibraryShell({ toolbar, content, inspector }: LibraryShellProps) {
  return (
    <section className="library-shell" aria-label="Library workspace">
      <div className="library-toolbar" role="toolbar" aria-label="Library toolbar">
        {toolbar}
      </div>
      <div className="library-body" data-inspector={inspector ? "open" : "closed"}>
        <div className="library-content">{content}</div>
        {inspector ? (
          <aside className="library-inspector" aria-label="Inspector">
            {inspector}
          </aside>
        ) : null}
      </div>
    </section>
  );
}

export default LibraryShell;
