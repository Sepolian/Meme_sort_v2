import { useCallback, useEffect, useRef, useState } from "react";
import type {
  LibraryDensity,
  LibraryMediaFilter,
  LibrarySort,
  LibraryStatusFilter,
} from "./libraryUrlState";
import { useEscapeSurface } from "../../components/useEscapeSurface";

interface LibraryControlsProps {
  sort: LibrarySort;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  density: LibraryDensity;
  onSortChange: (sort: LibrarySort) => void;
  onMediaChange: (media: LibraryMediaFilter) => void;
  onStatusChange: (status: LibraryStatusFilter) => void;
  onDensityChange: (density: LibraryDensity) => void;
  onClearFilters: () => void;
}

const MEDIA_OPTIONS: readonly { value: LibraryMediaFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "still", label: "Stills" },
  { value: "gif", label: "GIFs" },
];

/**
 * Library sorting, filtering, and density controls (ticket 08).
 *
 * Presentational only: all state flows through ticket 07's URL/preference
 * contract. The parent owns `useLibraryUrlState` and passes the effective
 * values plus setters, so this component never touches URL or storage
 * directly. URL-backed controls (`sort`/`media`/`status`) therefore
 * participate in back/forward navigation via the hook, while `density` is
 * persisted by ticket 07's store without a URL representation.
 */
export function LibraryControls({
  sort,
  media,
  status,
  density,
  onSortChange,
  onMediaChange,
  onStatusChange,
  onDensityChange,
  onClearFilters,
}: LibraryControlsProps) {
  const [viewOpen, setViewOpen] = useState(false);
  const viewMenuRef = useRef<HTMLDivElement | null>(null);
  const viewTriggerRef = useRef<HTMLButtonElement | null>(null);
  const statusFilterActive = status !== "all";

  const closeView = useCallback((restoreFocus: boolean) => {
    setViewOpen(false);
    if (restoreFocus) viewTriggerRef.current?.focus();
  }, []);

  useEscapeSurface(viewOpen, () => closeView(true));

  useEffect(() => {
    if (!viewOpen) return;
    const closeOnOutsidePointer = (event: MouseEvent) => {
      if (viewMenuRef.current && !viewMenuRef.current.contains(event.target as Node)) {
        closeView(false);
      }
    };
    document.addEventListener("mousedown", closeOnOutsidePointer);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsidePointer);
    };
  }, [closeView, viewOpen]);

  return (
    <div className="library-controls" aria-label="Library controls">
      <div className="library-media-tabs" role="group" aria-label="Media filter">
        {MEDIA_OPTIONS.map((option) => (
          <button
            key={option.value}
            className="library-media-tab"
            type="button"
            aria-pressed={media === option.value}
            onClick={() => onMediaChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className="library-view-menu" ref={viewMenuRef}>
        <button
          ref={viewTriggerRef}
          className="button button-secondary library-view-trigger"
          type="button"
          aria-label={statusFilterActive ? "View, status filter active" : "View"}
          aria-expanded={viewOpen}
          aria-controls="library-view-options"
          onClick={() => setViewOpen((open) => !open)}
        >
          View
          {statusFilterActive ? <span className="library-filter-indicator" aria-hidden="true">●</span> : null}
        </button>
        {viewOpen ? (
          <div
            className="library-view-dropdown"
            id="library-view-options"
            role="group"
            aria-label="View options"
          >
            <label className="library-control" htmlFor="library-sort">
              <span>Sort</span>
              <select
                id="library-sort"
                value={sort}
                onChange={(event) => onSortChange(event.target.value as LibrarySort)}
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="name">Name</option>
                <option value="type">Type</option>
                <option value="status">Status</option>
              </select>
            </label>
            <label className="library-control" htmlFor="library-status-filter">
              <span>Status</span>
              <select
                id="library-status-filter"
                value={status}
                onChange={(event) => onStatusChange(event.target.value as LibraryStatusFilter)}
              >
                <option value="all">All statuses</option>
                <option value="indexed">Indexed</option>
                <option value="pending">Pending</option>
                <option value="failed">Failed</option>
              </select>
            </label>
            <label className="library-control" htmlFor="library-density">
              <span>Density</span>
              <select
                id="library-density"
                value={density}
                onChange={(event) => onDensityChange(event.target.value as LibraryDensity)}
              >
                <option value="comfortable">Comfortable</option>
                <option value="compact">Compact</option>
              </select>
            </label>
            <button
              className="button button-secondary library-view-clear"
              type="button"
              disabled={media === "all" && status === "all"}
              onClick={onClearFilters}
            >
              Clear filters
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
