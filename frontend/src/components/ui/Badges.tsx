import type { WorkStatus } from "../../api/types";
import { WORK_STATUS_LABELS, WORK_STATUS_TONES, changeMarker } from "../../labels";

/** Shared treatment status: full pill, always a dot plus a label. */
export function TreatmentStatusPill({ value }: { value: WorkStatus }) {
  return (
    <span className={`pill pill-${WORK_STATUS_TONES[value]}`} data-testid={`status-${value}`}>
      <span className="pill-dot" aria-hidden="true" />
      {WORK_STATUS_LABELS[value]}
    </span>
  );
}

/** OmegaFlow status: neutral outline, read-only. Never coloured like a treatment status. */
export function OmegaFlowStatusTag({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <span className="tag">{value}</span>;
}

/** "Nouveau" / "Retour" / "Modification" marker derived from the unread kind returned by the API. */
export function ChangeMarker({ kind }: { kind: string | null | undefined }) {
  const marker = changeMarker(kind);
  if (!marker) return null;
  return <span className={`marker marker-${marker.tone}`}>{marker.label}</span>;
}

interface CountProps {
  value: number;
  tone?: "accent" | "neutral";
  /** Accessible description, e.g. "alertes non lues". */
  label?: string;
  hideZero?: boolean;
}

export function CountBadge({ value, tone = "accent", label, hideZero = true }: CountProps) {
  if (hideZero && !value) return null;
  return (
    <span className={`count count-${tone}`} aria-label={label ? `${value} ${label}` : undefined}>
      {value}
    </span>
  );
}
