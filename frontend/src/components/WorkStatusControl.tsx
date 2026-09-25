import { useId, useState } from "react";
import { ApiError } from "../api/client";
import { useSetWorkStatus } from "../api/hooks";
import type { WorkState, WorkStatus } from "../api/types";
import { WORK_STATUSES, WORK_STATUS_LABELS } from "../labels";

interface Props {
  membershipId: number;
  status: WorkStatus;
  version: number;
  label?: string;
}

/**
 * Shared work status of one dossier in one workflow. The version travels with every change
 * (optimistic locking): on a 409 the server's current state replaces the selection.
 */
export function WorkStatusControl({ membershipId, status, version, label = "Statut de traitement" }: Props) {
  const id = useId();
  const mutation = useSetWorkStatus();
  const [conflict, setConflict] = useState<WorkState | null>(null);
  const [saved, setSaved] = useState(false);
  const shown = conflict?.status ?? status;
  const currentVersion = conflict?.version ?? version;

  const change = (next: WorkStatus) => {
    setSaved(false);
    mutation.mutate(
      { membershipId, status: next, expectedVersion: currentVersion },
      {
        onSuccess: () => {
          setConflict(null);
          setSaved(true);
        },
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) {
            const current = (error.body as { current?: WorkState } | null)?.current;
            if (current) setConflict(current);
          }
        },
      },
    );
  };

  return (
    <div className="work-status">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={shown}
        disabled={mutation.isPending}
        onChange={(event) => change(event.target.value as WorkStatus)}
      >
        {WORK_STATUSES.map((s) => (
          <option key={s} value={s}>
            {WORK_STATUS_LABELS[s]}
          </option>
        ))}
      </select>
      <div role="status" aria-live="polite" className="status-message">
        {conflict && (
          <span className="error">
            Ce statut a été modifié entre-temps par {conflict.updated_by ?? "un collègue"} : il est maintenant «{" "}
            {WORK_STATUS_LABELS[conflict.status]} ». Refaites votre choix si nécessaire.
          </span>
        )}
        {saved && !conflict && <span className="ok">Statut enregistré.</span>}
        {mutation.isError && !conflict && <span className="error">{mutation.error.message}</span>}
      </div>
    </div>
  );
}
