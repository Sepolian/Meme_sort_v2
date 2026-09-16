import { useEffect, useRef, useState } from "react";

export type LibrarySearchMode = "meaning" | "filename";

interface LibrarySearchBarProps {
  query: string;
  isSearching: boolean;
  semanticBlocked?: boolean;
  isChoosingImage?: boolean;
  onQueryChange: (query: string) => void;
  onSubmit: (query: string) => void;
  onClear: () => void;
  onModeChange?: (mode: LibrarySearchMode, draft: string) => void;
  /** Attachment action beside the search bar: `chooseSearchImage` then `searchImage(requestId)` (ticket 12). */
  onImageSearch?: () => void;
}

/**
 * Single Library search bar (ticket 11) with image attachment (ticket 12).
 *
 * - By filename, typing updates `q` for instant local filtering; By meaning
 *   keeps typing in a draft and never calls `searchText` directly.
 * - Switching modes preserves the draft while the parent owns committed
 *   query/result transitions.
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
  onModeChange,
  onImageSearch,
}: LibrarySearchBarProps) {
  const [draft, setDraft] = useState(query);
  const [mode, setMode] = useState<LibrarySearchMode>(() =>
    query === "" ? "meaning" : "filename",
  );
  const [isComposing, setIsComposing] = useState(false);
  const isComposingRef = useRef(false);
  const lastCommittedRef = useRef(query);
  const preserveDraftRef = useRef(false);
  const preserveModeRef = useRef(false);
  const preserveModeQueryRef = useRef<string | null>(null);

  useEffect(() => {
    lastCommittedRef.current = query;
    const preserveDraft = preserveDraftRef.current;
    const preserveMode = preserveModeRef.current && preserveModeQueryRef.current === query;
    preserveDraftRef.current = false;
    preserveModeRef.current = false;
    preserveModeQueryRef.current = null;
    if (!isComposingRef.current && !preserveDraft) setDraft(query);
    if (!preserveMode) setMode(query === "" ? "meaning" : "filename");
  }, [query]);

  const preserveModeForQuery = (nextQuery: string) => {
    preserveModeRef.current = true;
    preserveModeQueryRef.current = nextQuery;
  };

  const commitQuery = (next: string) => {
    lastCommittedRef.current = next;
    setDraft(next);
    if (mode === "filename") preserveModeForQuery(next);
    onQueryChange(next);
  };

  const handleChange = (next: string, nativeIsComposing: boolean) => {
    setDraft(next);
    if (mode !== "filename" || isComposingRef.current || nativeIsComposing) return;
    if (next === lastCommittedRef.current) return;
    commitQuery(next);
  };

  const handleCompositionEnd = (next: string) => {
    isComposingRef.current = false;
    setIsComposing(false);
    if (mode !== "filename" || next === lastCommittedRef.current) {
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
    if (mode === "filename") preserveModeForQuery("");
    onClear();
  };

  const handleModeChange = (nextMode: LibrarySearchMode) => {
    setMode(nextMode);
    const queryWillChange = nextMode === "filename" ? query !== draft : query !== "";
    if (queryWillChange) {
      preserveDraftRef.current = true;
      preserveModeForQuery(nextMode === "filename" ? draft : "");
    }
    onModeChange?.(nextMode, draft);
  };

  const trimmed = draft.trim();
  const hasUnsubmittedMeaningDraft = mode === "meaning" && trimmed !== "" && draft !== query;
  // Ticket 11: a new submit must stay available while a previous request is
  // still waiting so it can cancel obsolete work via `cancelSearch(previous)`.
  // Only empty queries and health-blocked semantic work disable submit.
  // Ticket 12: the image attachment stays available during searching for the
  // same cancel-oldest reason; only health-blocked work disables it.
  const submitDisabled =
    mode !== "meaning" || trimmed === "" || semanticBlocked || isComposing;
  const imageDisabled = semanticBlocked || isChoosingImage;

  return (
    <div className="library-search-bar">
      <form
        role="search"
        aria-label="Library search"
        onSubmit={(event) => {
          event.preventDefault();
          if (
            mode !== "meaning" ||
            trimmed === "" ||
            semanticBlocked ||
            isComposingRef.current
          ) return;
          if (query !== trimmed) preserveModeForQuery(trimmed);
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
            placeholder={
              mode === "meaning"
                ? "Describe the reaction to search by meaning"
                : "Filter by filename or primary source path"
            }
            autoComplete="off"
          />
          <select
            className="library-search-mode"
            aria-label="Search mode"
            value={mode}
            onChange={(event) =>
              handleModeChange(event.target.value as LibrarySearchMode)
            }
          >
            <option value="meaning">By meaning</option>
            <option value="filename">By filename</option>
          </select>
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
      {hasUnsubmittedMeaningDraft ? (
        <p className="library-search-context" role="note">
          {query
            ? <>Showing results for &ldquo;{query}&rdquo;. &ldquo;{draft}&rdquo; is a draft; choose Search to update the Library.</>
            : <>Showing the current Library results. &ldquo;{draft}&rdquo; is a draft; choose Search to update the Library.</>}
        </p>
      ) : null}
      {semanticBlocked ? (
        <p role="note">
          Semantic search is unavailable until the current session passes the Runtime health
          check. {mode === "filename"
            ? "By filename still filters the loaded Library locally."
            : "By meaning keeps your draft local without filtering the Library."}
        </p>
      ) : null}
    </div>
  );
}
