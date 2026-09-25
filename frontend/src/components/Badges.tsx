import type { ReactNode } from "react";
import type { NotificationClass, RulesStatus, WorkStatus } from "../api/types";
import { CLASS_LABELS, EVENT_LABELS, POLL_LABELS, RULES_LABELS, UNREAD_KIND_LABELS, WORK_STATUS_LABELS } from "../labels";

/** ACTION and INFORMATIONAL are told apart by an icon and a word, never by colour alone. */
export function ClassBadge({ value }: { value: NotificationClass }) {
  const icon = value === "ACTION" ? "⚡" : value === "INFORMATIONAL" ? "ℹ" : "🔇";
  return (
    <span className={`badge badge-class-${value.toLowerCase()}`} data-testid={`class-${value}`}>
      <span aria-hidden="true">{icon}</span> {CLASS_LABELS[value]}
    </span>
  );
}

export function RulesBadge({ value }: { value: RulesStatus }) {
  if (value === "CONFIRMED") return null;
  return (
    <span className={`badge badge-rules-${value.toLowerCase()}`} title="Règle dérivée des captures, à confirmer avec l’agence">
      {RULES_LABELS[value]}
    </span>
  );
}

export function WorkStatusBadge({ value }: { value: WorkStatus }) {
  return <span className={`badge badge-work-${value.toLowerCase()}`}>{WORK_STATUS_LABELS[value]}</span>;
}

export function UnreadBadge({ kind }: { kind: string | null }) {
  if (!kind) return null;
  return (
    <span className="badge badge-unread">
      <span aria-hidden="true">●</span> {UNREAD_KIND_LABELS[kind] ?? "Non lu"}
    </span>
  );
}

export function PollBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="badge">Jamais lu</span>;
  const icon = status === "COMPLETE" ? "✓" : status === "PARTIAL" ? "!" : "✕";
  return (
    <span className={`badge badge-poll-${status.toLowerCase()}`}>
      <span aria-hidden="true">{icon}</span> {POLL_LABELS[status] ?? status}
    </span>
  );
}

export function EventKind({ kind, cls }: { kind: string; cls: NotificationClass }) {
  return (
    <span className="event-kind">
      <ClassBadge value={cls} /> {EVENT_LABELS[kind] ?? kind}
    </span>
  );
}

export function Count({ value, label, children }: { value: number; label: string; children?: ReactNode }) {
  if (!value) return null;
  return (
    <span className="count" aria-label={`${value} ${label}`}>
      {value}
      {children}
    </span>
  );
}
