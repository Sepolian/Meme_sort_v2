import type {
  LibraryDensity,
  LibraryMediaFilter,
  LibrarySort,
  LibraryStatusFilter,
} from "./libraryUrlState";

interface LibraryControlsProps {
  sort: LibrarySort;
  media: LibraryMediaFilter;
  status: LibraryStatusFilter;
  density: LibraryDensity;
  onSortChange: (sort: LibrarySort) => void;
  onMediaChange: (media: LibraryMediaFilter) => void;
  onStatusChange: (status: LibraryStatusFilter) => void;
  onDensityChange: (density: LibraryDensity) => void;
}

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
}: LibraryControlsProps) {
  return (
    <div className="library-controls" aria-label="Library sort, filter, and density controls">
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
      <label className="library-control" htmlFor="library-media-filter">
        <span>Media</span>
        <select
          id="library-media-filter"
          value={media}
          onChange={(event) => onMediaChange(event.target.value as LibraryMediaFilter)}
        >
          <option value="all">All media</option>
          <option value="still">Still images</option>
          <option value="gif">GIFs</option>
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
    </div>
  );
}

export default LibraryControls;
