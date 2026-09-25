import { BellOff, CircleAlert, CircleCheck, Info, TriangleAlert, Zap } from "lucide-react";
import type { NotificationClass, RulesStatus } from "../api/types";
import { CLASS_LABELS, EVENT_LABELS, POLL_LABELS, RULES_LABELS } from "../labels";

/** ACTION and INFORMATIONAL are told apart by an icon and a word, never by colour alone. */
export function ClassBadge({ value }: { value: NotificationClass }) {
  const Icon = value === "ACTION" ? Zap : value === "INFORMATIONAL" ? Info : BellOff;
  return (
    <span className={`badge badge-class-${value.toLowerCase()}`} data-testid={`class-${value}`}>
      <Icon size={12} aria-hidden="true" /> {CLASS_LABELS[value]}
    </span>
  );
}

/** Uncertain configuration: shown in administration and once per queue page, never per navigation row. */
export function RulesBadge({ value }: { value: RulesStatus }) {
  if (value === "CONFIRMED") return null;
  return (
    <span className="badge badge-warn" title="Règle dérivée des captures, à confirmer avec l’agence">
      <TriangleAlert size={12} aria-hidden="true" /> {RULES_LABELS[value]}
    </span>
  );
}

export function PollBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="badge badge-neutral">Jamais lu</span>;
  const tone = status === "COMPLETE" ? "ok" : status === "PARTIAL" ? "warn" : "danger";
  const Icon = status === "COMPLETE" ? CircleCheck : status === "PARTIAL" ? TriangleAlert : CircleAlert;
  return (
    <span className={`badge badge-${tone}`}>
      <Icon size={12} aria-hidden="true" /> {POLL_LABELS[status] ?? status}
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
