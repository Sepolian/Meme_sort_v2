import { useEffect, useRef, useState } from "react";

interface LibrarySearchBarProps {
  query: string;
  isSearching: boolean;
  semanticBlocked?: boolean;
  isChoosingImage?: boolean;
  onQueryChange: (query: string) => void;
  onSubmit: (query: string) => void;
  onClear: () => void;
  /** Attachment action beside the search bar: `chooseSearchImage` then `searchImage(requestId)` (ticket 12). */
  onImageSearch?: () => void;
}

/**
 * Single Library search bar (ticket 11) with image attachment (ticket 12).
 *
 * - Typing updates `q` only (instant local filtering); it never calls
 *   `searchText` directly.
 * - IME composition stays in a local draft and commits only when composition
 *   ends, so URL-backed rerenders cannot disturb Pinyin input.
 * - Enter or the explicit Search button starts one UUID-scoped semantic
 *   Search Request via `onSubmit`.
 * - The attachment button beside the bar runs the native picker via
 *   `onImageSearch`; picker cancel leaves state unchanged (parent-owned).
 * - Clear restores browsing and cancels active work via `onClear`.
 */
export function LibrarySearchBar({
  query,
  isSearching,
  semanticBlocked = false,
  isChoosingImage = false,
  onQueryChange,
  onSubmit,
  onClear,
  onImageSearch,
}: LibrarySearchBarProps) {
  const [draft, setDraft] = useState(query);
  const [isComposing, setIsComposing] = useState(false);
  const isComposingRef = useRef(false);
  const lastCommittedRef = useRef(query);

  useEffect(() => {
    lastCommittedRef.current = query;
    if (!isComposingRef.current) setDraft(query);
  }, [query]);

  const commitQuery = (next: string) => {
    lastCommittedRef.current = next;
    setDraft(next);
    onQueryChange(next);
  };

  const handleChange = (next: string, nativeIsComposing: boolean) => {
    setDraft(next);
    if (isComposingRef.current || nativeIsComposing) return;
    if (next === lastCommittedRef.current) return;
    commitQuery(next);
  };

  const handleCompositionEnd = (next: string) => {
    isComposingRef.current = false;
    setIsComposing(false);
    if (next === lastCommittedRef.current) {
      setDraft(next);
      return;
    }
    commitQuery(next);
  };

  const handleClear = () => {
    isComposingRef.current = false;
    setIsComposing(false);
    lastCommittedRef.current = "";
    setDraft("");
    onClear();
  };

  const trimmed = draft.trim();
  // Ticket 11: a new submit must stay available while a previous request is
  // still waiting so it can cancel obsolete work via `cancelSearch(previous)`.
  // Only empty queries and health-blocked semantic work disable submit.
  // Ticket 12: the image attachment stays available during searching for the
  // same cancel-oldest reason; only health-blocked work disables it.
  const submitDisabled = trimmed === "" || semanticBlocked || isComposing;
  const imageDisabled = semanticBlocked || isChoosingImage;

  return (
    <div className="library-search-bar">
      <form
        role="search"
        aria-label="Library search"
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmed === "" || semanticBlocked || isComposingRef.current) return;
          onSubmit(draft);
        }}
      >
        <label htmlFor="library-search-input">Search Library</label>
        <div className="library-search-row">
          <input
            id="library-search-input"
            value={draft}
            onChange={(event) =>
              handleChange(
                event.currentTarget.value,
                (event.nativeEvent as InputEvent).isComposing,
              )
            }
            onCompositionStart={() => {
              isComposingRef.current = true;
              setIsComposing(true);
            }}
            onCompositionEnd={(event) => handleCompositionEnd(event.currentTarget.value)}
            placeholder="Filter by name or source path, Enter for semantic search"
            autoComplete="off"
          />
          <button className="button" type="submit" disabled={submitDisabled}>
            {isSearching ? "Searching…" : "Search"}
          </button>
          {onImageSearch ? (
            <button
              className="button button-secondary"
              type="button"
              aria-label="Image search"
              title="Choose an image to search with"
              disabled={imageDisabled}
              onClick={onImageSearch}
            >
              {isChoosingImage ? "Choosing…" : "Image"}
            </button>
          ) : null}
          {draft !== "" ? (
            <button className="button button-secondary" type="button" onClick={handleClear}>
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
