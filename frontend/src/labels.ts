import type { NotificationClass, RulesStatus, WorkStatus } from "./api/types";

export const WORK_STATUS_LABELS: Record<WorkStatus, string> = {
  TO_DO: "À traiter",
  IN_PROGRESS: "En cours",
  WAITING: "En attente",
  DONE: "Terminé",
};
export const WORK_STATUSES = Object.keys(WORK_STATUS_LABELS) as WorkStatus[];
/** Token suffix (--st-<tone>-*) of each treatment status. */
export const WORK_STATUS_TONES: Record<WorkStatus, "todo" | "prog" | "wait" | "done"> = {
  TO_DO: "todo",
  IN_PROGRESS: "prog",
  WAITING: "wait",
  DONE: "done",
};

export const CLASS_LABELS: Record<NotificationClass, string> = {
  ACTION: "Action",
  INFORMATIONAL: "Information",
  SILENT: "Silencieux",
};

export const RULES_LABELS: Record<RulesStatus, string> = {
  UNCONFIRMED: "Inactif (non confirmé)",
  CAPTURE_DERIVED: "À valider sur site",
  CONFIRMED: "Confirmé",
};

export const EVENT_LABELS: Record<string, string> = {
  WORKFLOW_ITEM_NEW: "Nouveau dossier",
  WORKFLOW_ITEM_RETURNED: "Retour dans la file",
  WORKFLOW_ITEM_CHANGED: "Modification",
  WORKFLOW_ITEM_COMPLETED: "Arrivée (information)",
  WORKFLOW_ITEM_TRANSITION: "Transition",
  WORKFLOW_ITEM_LEFT: "Sorti de la file",
  SESSION_AUTH_REQUIRED: "Session OmegaFlow à reconnecter",
  WORKFLOW_POLL_PARTIAL: "Lecture partielle",
  WORKFLOW_POLL_FAILED: "Échec de lecture",
};

export const POLL_LABELS: Record<string, string> = {
  COMPLETE: "Complète",
  PARTIAL: "Partielle",
  AUTH_REQUIRED: "Reconnexion requise",
  FAILED: "Échec",
};

export const UNREAD_KIND_LABELS: Record<string, string> = {
  WORKFLOW_ITEM_NEW: "Nouveau",
  NEW_AGREEMENT_DOSSIER: "Nouveau",
  WORKFLOW_ITEM_RETURNED: "Retour",
  WORKFLOW_ITEM_CHANGED: "Modification",
};

/** Marker shown beside a dossier with an unread occurrence; null when nothing is unread. */
export function changeMarker(kind: string | null | undefined): { tone: "new" | "mod"; label: string } | null {
  if (!kind) return null;
  const label = UNREAD_KIND_LABELS[kind] ?? "Non lu";
  return { tone: kind === "WORKFLOW_ITEM_CHANGED" ? "mod" : "new", label };
}

const dateFmt = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" });
export function formatDateTime(value: string | null | undefined): string {
  return value ? dateFmt.format(new Date(value)) : "—";
}
export function formatAge(days: number): string {
  if (days <= 0) return "aujourd’hui";
  return days === 1 ? "depuis 1 jour" : `depuis ${days} jours`;
}

const timeFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
export function formatTime(value: string | null | undefined): string {
  return value ? timeFmt.format(new Date(value)) : "—";
}
