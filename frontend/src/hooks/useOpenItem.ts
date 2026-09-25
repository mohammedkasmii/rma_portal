import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useAcknowledge } from "../api/hooks";
import type { ItemView } from "../api/types";

/**
 * Opening a list row is the explicit act that acknowledges its occurrence, for this employee only.
 * Opening the dossier never depends on the acknowledgement succeeding.
 */
export function useOpenItem(onAcknowledged?: (item: ItemView) => void) {
  const acknowledge = useAcknowledge();
  const navigate = useNavigate();
  return useCallback(
    async (item: ItemView) => {
      if (item.unread) {
        try {
          await acknowledge.mutateAsync(item.occurrence_id);
          onAcknowledged?.(item);
        } catch {
          /* opening the dossier must never depend on the acknowledgement */
        }
      }
      void navigate(`/dossiers/${item.dossier_id}?membership=${item.membership_id}`);
    },
    [acknowledge, navigate, onAcknowledged],
  );
}
