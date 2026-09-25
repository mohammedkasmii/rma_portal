import { useState, type KeyboardEvent } from "react";
import { ApiError } from "../../api/client";
import { useSetWorkStatus } from "../../api/hooks";
import type { WorkState, WorkStatus } from "../../api/types";
import { WORK_STATUSES, WORK_STATUS_LABELS, WORK_STATUS_TONES } from "../../labels";
import { useToast } from "./Toast";

interface Props {
  membershipId: number;
  status: WorkStatus;
  version: number;
  /** Accessible name of the control. */
  label?: string;
  /** "segmented" on the dossier page, "select" inside lists and previews. */
  variant?: "segmented" | "select";
}

/**
 * Shared treatment status of one dossier in one file. The version travels with every change
 * (optimistic locking). The new value shows immediately and rolls back on failure; on a 409 the
 * server's current state replaces the selection. A toast offers to undo for five seconds.
 */
export function TreatmentStatusControl({
  membershipId,
  status,
  version,
  label = "Statut de traitement",
  variant = "select",
}: Props) {
  const mutation = useSetWorkStatus();
  const toast = useToast();
  const [optimistic, setOptimistic] = useState<WorkStatus | null>(null);
  const [known, setKnown] = useState<{ status: WorkStatus; version: number } | null>(null);
  const [conflict, setConflict] = useState<WorkState | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  // A newer server state (conflict answer or our own save) wins over the props until they catch up.
  const base = known && known.version > version ? known : { status, version };
  const shown = optimistic ?? base.status;

  const change = (next: WorkStatus, previous: WorkStatus = base.status, allowUndo = true) => {
    if (next === base.status) return;
    setOptimistic(next);
    setFailure(null);
    setConflict(null);
    mutation.mutate(
      { membershipId, status: next, expectedVersion: base.version },
      {
        onSuccess: (state) => {
          setKnown({ status: state.status, version: state.version });
          setOptimistic(null);
          toast({
            message: `Statut passé à « ${WORK_STATUS_LABELS[state.status]} ».`,
            action: allowUndo
              ? {
                  label: "Annuler",
                  onSelect: () => {
                    setOptimistic(previous);
                    mutation.mutate(
                      { membershipId, status: previous, expectedVersion: state.version },
                      {
                        onSuccess: (undone) => {
                          setKnown({ status: undone.status, version: undone.version });
                          setOptimistic(null);
                        },
                        onError: () => setOptimistic(null),
                      },
                    );
                  },
                }
              : undefined,
          });
        },
        onError: (error) => {
          setOptimistic(null);
          if (error instanceof ApiError && error.status === 409) {
            const current = (error.body as { current?: WorkState } | null)?.current;
            if (current) {
              setConflict(current);
              setKnown({ status: current.status, version: current.version });
              return;
            }
          }
          setFailure(error.message);
          toast({ tone: "error", message: "Le statut n’a pas pu être enregistré." });
        },
      },
    );
  };

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = WORK_STATUSES.indexOf(shown);
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % WORK_STATUSES.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + WORK_STATUSES.length) % WORK_STATUSES.length;
    else return;
    event.preventDefault();
    change(WORK_STATUSES[next]);
    const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]");
    buttons[next]?.focus();
  };

  return (
    <div className="status-control">
      {variant === "segmented" ? (
        // eslint-disable-next-line jsx-a11y/interactive-supports-focus -- focus lives on the radio buttons (roving tabindex)
        <div role="radiogroup" aria-label={label} className="segmented" onKeyDown={onKey}>
          {WORK_STATUSES.map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={shown === value}
              tabIndex={shown === value ? 0 : -1}
              disabled={mutation.isPending}
              className={`segmented-option seg-${WORK_STATUS_TONES[value]}`}
              onClick={() => change(value)}
            >
              <span className="pill-dot" aria-hidden="true" />
              {WORK_STATUS_LABELS[value]}
            </button>
          ))}
        </div>
      ) : (
        <span className={`status-select pill pill-${WORK_STATUS_TONES[shown]}`}>
          <span className="pill-dot" aria-hidden="true" />
          <select
            aria-label={label}
            value={shown}
            disabled={mutation.isPending}
            onChange={(event) => change(event.target.value as WorkStatus)}
          >
            {WORK_STATUSES.map((value) => (
              <option key={value} value={value}>
                {WORK_STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </span>
      )}
      <div role="status" aria-live="polite" className="status-message">
        {conflict && (
          <span className="error-text">
            Ce statut a été modifié entre-temps par {conflict.updated_by ?? "un collègue"} : il est maintenant «{" "}
            {WORK_STATUS_LABELS[conflict.status]} ». Refaites votre choix si nécessaire.
          </span>
        )}
        {failure && !conflict && <span className="error-text">{failure}</span>}
      </div>
    </div>
  );
}
