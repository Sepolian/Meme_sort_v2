interface LibrarySearchBarProps {
  query: string;
  isSearching: boolean;
  semanticBlocked?: boolean;
  onQueryChange: (query: string) => void;
  onSubmit: (query: string) => void;
  onClear: () => void;
}

/**
 * Single Library search bar (ticket 11).
 *
 * - Typing updates `q` only (instant local filtering); it never calls
 *   `searchText` directly.
 * - Enter or the explicit Search button starts one UUID-scoped semantic
 *   Search Request via `onSubmit`.
 * - Clear restores browsing and cancels active work via `onClear`.
 */
export function LibrarySearchBar({
  query,
  isSearching,
  semanticBlocked = false,
  onQueryChange,
  onSubmit,
  onClear,
}: LibrarySearchBarProps) {
  const trimmed = query.trim();
  // Ticket 11: a new submit must stay available while a previous request is
  // still waiting so it can cancel obsolete work via `cancelSearch(previous)`.
  // Only empty queries and health-blocked semantic work disable submit.
  const submitDisabled = trimmed === "" || semanticBlocked;

  return (
    <div className="library-search-bar">
      <form
        role="search"
        aria-label="Library search"
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmed === "" || semanticBlocked) return;
          onSubmit(query);
        }}
      >
        <label htmlFor="library-search-input">Search Library</label>
        <div className="library-search-row">
          <input
            id="library-search-input"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Filter by name or source path, Enter for semantic search"
            autoComplete="off"
          />
          <button className="button" type="submit" disabled={submitDisabled}>
            {isSearching ? "Searching…" : "Search"}
          </button>
          {query !== "" ? (
            <button className="button button-secondary" type="button" onClick={onClear}>
              Clear
            </button>
          ) : null}
        </div>
      </form>
      {semanticBlocked ? (
        <p role="note">
          Semantic search is unavailable until the current session passes the Runtime health
          check. Typing still filters the loaded Library locally.
        </p>
      ) : null}
    </div>
  );
}

export default LibrarySearchBar;
