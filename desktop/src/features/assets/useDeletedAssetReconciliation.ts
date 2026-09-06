import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AssetListResult } from "../../api/types";

export type DeletedAssetReconciler = (deletedAssetIds: readonly string[]) => Promise<void>;

/**
 * Reconciles the frontend state that follows a backend-confirmed deletion.
 *
 * Single delete, batch delete and the Orphan Asset branch of removing the last
 * Source Record all report through this one rule, with the Asset IDs the
 * backend confirmed as deleted. Callers keep ownership of selection,
 * confirmation and which Asset the detail shows; this hook only applies the
 * confirmed result and refreshes what the deletion invalidated.
 */
export function useDeletedAssetReconciliation({ inspectedAssetId, onCloseDetail }: {
  /** Asset ID the open detail currently targets, or null when none is open. */
  inspectedAssetId: string | null;
  /** Closes the open detail. */
  onCloseDetail: () => void;
}): DeletedAssetReconciler {
  const queryClient = useQueryClient();
  const inspectedRef = useRef(inspectedAssetId);
  const closeDetailRef = useRef(onCloseDetail);

  // Read at resolution time so a detail opened while deletion was in flight is
  // never closed by the older request.
  useEffect(() => {
    inspectedRef.current = inspectedAssetId;
  }, [inspectedAssetId]);
  useEffect(() => {
    closeDetailRef.current = onCloseDetail;
  }, [onCloseDetail]);

  return useCallback(
    async (deletedAssetIds: readonly string[]) => {
      const deleted = new Set(deletedAssetIds);
      if (deleted.size) {
        // Confirmed removal is applied before any refresh so the result is
        // visible even while the refetch is still pending.
        queryClient.setQueryData<AssetListResult | undefined>(["assets"], (old) =>
          old ? { ...old, assets: old.assets.filter((item) => !deleted.has(item.asset_id)) } : old,
        );
        if (inspectedRef.current && deleted.has(inspectedRef.current)) {
          closeDetailRef.current();
        }
      }
      // A failed refresh cannot undo a confirmed deletion, and it is not a
      // deletion failure, so it never propagates to the caller. Refreshing
      // never touches a deleted Asset's detail: that would only manufacture an
      // avoidable error.
      try {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["assets"] }),
          queryClient.invalidateQueries({ queryKey: ["app-state"] }),
        ]);
      } catch {
        // The Library list and App State stay stale until the next refresh.
      }
      // Dropped after the refresh, when a closed detail has released its
      // subscription. A detail that is still open is left alone: dropping it
      // would rebuild the query and refetch the Asset just deleted.
      for (const assetId of deleted) {
        const detail = queryClient.getQueryCache().find({ queryKey: ["asset-detail", assetId] });
        if (!detail || detail.isActive()) continue;
        queryClient.removeQueries({ queryKey: ["asset-detail", assetId] });
      }
    },
    [queryClient],
  );
}
