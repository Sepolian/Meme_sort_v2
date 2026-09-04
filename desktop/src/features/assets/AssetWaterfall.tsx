import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { AssetSummary } from "../../api/types";
import { mediaUrl } from "../../api/media-url";
import { getAssetDisplayName, isGifAsset } from "../library/libraryOrdering";
import type { LibraryDensity } from "../library/libraryUrlState";
import {
  WATERFALL_LAZY_ROOT_MARGIN,
  assignWaterfallColumns,
  estimateWaterfallItemHeight,
  getAssetAspectRatioStyle,
  getWaterfallColumnCount,
} from "./waterfall";

interface AssetWaterfallProps {
  /** Already sorted input (ticket 08 order); consumed in order, never re-sorted. */
  assets: readonly AssetSummary[];
  density: LibraryDensity;
  checkedIds: ReadonlySet<string>;
  onOpenAsset: (assetId: string) => void;
  onToggleChecked: (assetId: string) => void;
  /**
   * Parent-owned ref to the wall section (used for native-drag hit-testing).
   * The waterfall only reads it for measurement/scroll preservation.
   */
  sectionRef?: React.RefObject<HTMLDivElement | null>;
  /** Native-drag hover state for the accepting outline. */
  accepting?: boolean;
  /** Fixed column count for tests; otherwise measured from container width. */
  columnCount?: number;
}

function formatDimensions(asset: AssetSummary): string {
  return asset.width && asset.height
    ? `${asset.width} × ${asset.height}`
    : "Dimensions unavailable";
}

function formatStatusBadge(status: AssetSummary["status"]): string {
  return `${status.charAt(0).toUpperCase()}${status.slice(1)} Asset`;
}

/**
 * Near-viewport lazy media with reserved geometry (ticket 09).
 *
 * The wrapper always carries the inline aspect-ratio reservation (with the
 * 1:1 fallback), so thumbnail completion never relocates cards. No `<img
 * src>` is rendered until an IntersectionObserver with
 * `WATERFALL_LAZY_ROOT_MARGIN` reports the card near the viewport, so media
 * outside the margin is never requested eagerly. Environments without
 * IntersectionObserver (jsdom tests) activate immediately so content remains
 * testable without a mock.
 */
function LazyAssetMedia({
  asset,
  src,
  alt,
}: {
  asset: AssetSummary;
  src: string | undefined;
  alt: string;
}) {
  const [isNearViewport, setIsNearViewport] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const reservation = useMemo(
    () => getAssetAspectRatioStyle(asset),
    // Width/height are the only inputs to the reservation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [asset.width, asset.height],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (typeof IntersectionObserver === "undefined") {
      setIsNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setIsNearViewport(true);
          observer.disconnect();
        }
      },
      { rootMargin: WATERFALL_LAZY_ROOT_MARGIN },
    );
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={hostRef}
      className="asset-card-media-wrap"
      style={reservation}
      data-asset-id={asset.asset_id}
    >
      {isNearViewport && src ? (
        <img
          className="asset-card-media"
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
        />
      ) : (
        <div
          className="asset-card-media-placeholder media-placeholder"
          aria-hidden="true"
        />
      )}
    </div>
  );
}

interface AssetWaterfallCardProps {
  asset: AssetSummary;
  columnIndex: number;
  checked: boolean;
  gifActive: boolean;
  onOpen: (assetId: string) => void;
  onToggle: (assetId: string) => void;
  onGifActivate: (assetId: string) => void;
  onGifDeactivate: (assetId: string) => void;
}

/**
 * One waterfall card (ticket 09).
 *
 * - Media is uncropped (`object-fit: contain` inside the reserved wrapper).
 * - Pending/Failed badges are always visible; normal Indexed state is hidden.
 * - Name, quick actions, and the selection checkbox live in the hover overlay
 *   (revealed on hover and on focus-within for keyboard users). The overlay
 *   uses opacity rather than display so showing it never shifts geometry.
 * - GIF cards render the static thumbnail by default and swap only the active
 *   card to the managed GIF URL on hover/focus; the parent guarantees at most
 *   one animated source via `gifActive`. This is an image-source swap, not a
 *   video-pause API.
 */
function AssetWaterfallCard({
  asset,
  columnIndex,
  checked,
  gifActive,
  onOpen,
  onToggle,
  onGifActivate,
  onGifDeactivate,
}: AssetWaterfallCardProps) {
  const name = getAssetDisplayName(asset);
  const gif = isGifAsset(asset);
  const thumbnailSrc =
    mediaUrl(asset.thumbnail_url) ?? mediaUrl(asset.library_url);
  const gifSrc = mediaUrl(asset.library_url);
  // Static thumbnail by default; only the singleton-active GIF card uses its
  // managed GIF URL. Missing GIF sources fall back to the thumbnail.
  const src = gif && gifActive ? (gifSrc ?? thumbnailSrc) : thumbnailSrc;
  // Indexed stays visually quiet; every other status keeps a visible badge.
  const showBadge = asset.status !== "indexed";

  return (
    <article
      className="asset-card"
      data-asset-id={asset.asset_id}
      data-column={columnIndex}
      data-gif={gif ? "true" : "false"}
      data-gif-active={gif && gifActive ? "true" : "false"}
      onMouseEnter={gif ? () => onGifActivate(asset.asset_id) : undefined}
      onMouseLeave={gif ? () => onGifDeactivate(asset.asset_id) : undefined}
      onFocus={gif ? () => onGifActivate(asset.asset_id) : undefined}
      onBlur={
        gif
          ? (event) => {
              if (
                !event.currentTarget.contains(event.relatedTarget as Node | null)
              ) {
                onGifDeactivate(asset.asset_id);
              }
            }
          : undefined
      }
    >
      <button
        className="asset-card-open"
        type="button"
        onClick={() => onOpen(asset.asset_id)}
        aria-label={`Open ${name}`}
      >
        <LazyAssetMedia asset={asset} src={src} alt={`${name} preview`} />
      </button>
      {showBadge ? (
        <span
          className={`status-pill status-${asset.status} asset-card-badge`}
          aria-label={formatStatusBadge(asset.status)}
        >
          {formatStatusBadge(asset.status)}
        </span>
      ) : null}
      <div className="asset-card-hover">
        <span className="asset-card-name">{name}</span>
        <span className="asset-card-meta">
          {formatDimensions(asset)} · {asset.media_type}
        </span>
        <div
          className="asset-card-quick-actions"
          aria-label={`Quick actions for ${name}`}
        >
          <button
            className="button button-secondary asset-card-quick-view"
            type="button"
            onClick={() => onOpen(asset.asset_id)}
          >
            View
          </button>
        </div>
        <label className="asset-select">
          <input
            type="checkbox"
            checked={checked}
            onChange={() => onToggle(asset.asset_id)}
          />{" "}
          Select {name}
        </label>
      </div>
    </article>
  );
}

/**
 * Remember the nearest `.library-content` scroll position and restore it when
 * a re-render without a route replacement resets the container (route changes
 * unmount this component, dropping the saved position by design). Combined
 * with stable `asset_id` keys and reserved card geometry, surrounding Library
 * state changes (selection, drag cues, import feedback) keep scroll position.
 */
function usePreserveLibraryScroll(
  sectionRef: React.RefObject<HTMLDivElement | null> | undefined,
) {
  const savedScrollRef = useRef<{ top: number; left: number } | null>(null);

  useEffect(() => {
    const scroller = sectionRef?.current?.closest(
      ".library-content",
    ) as HTMLElement | null;
    if (!scroller) return;
    const onScroll = () => {
      savedScrollRef.current = {
        top: scroller.scrollTop,
        left: scroller.scrollLeft,
      };
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [sectionRef]);

  useLayoutEffect(() => {
    const scroller = sectionRef?.current?.closest(
      ".library-content",
    ) as HTMLElement | null;
    const saved = savedScrollRef.current;
    if (
      scroller &&
      saved &&
      saved.top !== 0 &&
      scroller.scrollTop === 0 &&
      scroller.scrollTop !== saved.top
    ) {
      scroller.scrollTop = saved.top;
      scroller.scrollLeft = saved.left;
    }
  });
}

export function AssetWaterfall({
  assets,
  density,
  checkedIds,
  onOpenAsset,
  onToggleChecked,
  sectionRef,
  accepting = false,
  columnCount,
}: AssetWaterfallProps) {
  // Singleton animated GIF: activating another card first restores the
  // previous one because `src` derives from this single id.
  const [activeGifId, setActiveGifId] = useState<string | null>(null);
  const activateGif = useCallback((assetId: string) => {
    setActiveGifId(assetId);
  }, []);
  const deactivateGif = useCallback((assetId: string) => {
    setActiveGifId((current) => (current === assetId ? null : current));
  }, []);
  // A filtered-out or removed Asset must never pin the singleton slot.
  const effectiveActiveGifId = assets.some(
    (asset) => asset.asset_id === activeGifId,
  )
    ? activeGifId
    : null;

  const [measuredWidth, setMeasuredWidth] = useState(0);
  // Measure synchronously after mount so the first paint already uses the
  // real column count (no 1-column flash). Unmeasurable containers (width 0,
  // jsdom) stay single-column so DOM order preserves input order there.
  useLayoutEffect(() => {
    const element = sectionRef?.current;
    if (element && element.clientWidth > 0) {
      setMeasuredWidth(element.clientWidth);
    }
  }, [sectionRef]);
  useEffect(() => {
    const element = sectionRef?.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? element.clientWidth;
      if (width > 0) setMeasuredWidth(width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [sectionRef]);

  usePreserveLibraryScroll(sectionRef);

  const resolvedColumnCount =
    columnCount ??
    (measuredWidth > 0
      ? getWaterfallColumnCount(measuredWidth, density)
      : 1);

  const columns = useMemo(
    () =>
      assignWaterfallColumns(assets, resolvedColumnCount, (item) =>
        estimateWaterfallItemHeight(item),
      ),
    [assets, resolvedColumnCount],
  );

  return (
    <section
      className={`asset-grid${accepting ? " asset-grid-accepting" : ""}`}
      aria-label="Assets"
      data-density={density}
      data-column-count={resolvedColumnCount}
      ref={sectionRef}
    >
      {columns.map((columnItems, columnIndex) => (
        <div
          key={columnIndex}
          className="asset-waterfall-column"
          data-column={columnIndex}
        >
          {columnItems.map((asset) => (
            <AssetWaterfallCard
              key={asset.asset_id}
              asset={asset}
              columnIndex={columnIndex}
              checked={checkedIds.has(asset.asset_id)}
              gifActive={effectiveActiveGifId === asset.asset_id}
              onOpen={onOpenAsset}
              onToggle={onToggleChecked}
              onGifActivate={activateGif}
              onGifDeactivate={deactivateGif}
            />
          ))}
        </div>
      ))}
    </section>
  );
}

export default AssetWaterfall;
