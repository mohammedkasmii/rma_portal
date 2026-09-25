import type { Dashboard, SessionHealth } from "./api/types";
import { formatTime } from "./labels";

export type SyncLevel = "ok" | "stale" | "expired";

export interface SyncStatus {
  level: SyncLevel;
  /** Short text for the top bar. */
  label: string;
  /** Formatted time of the last successful read, when known. */
  lastRefresh: string | null;
}

/** Derives the portal-wide freshness indicator from what /dashboard already returns. */
export function syncStatus(session: SessionHealth | undefined, problemWorkflows: number): SyncStatus {
  if (!session) return { level: "stale", label: "Actualisation…", lastRefresh: null };
  const at = session.last_success_at ?? session.last_poll_at;
  const lastRefresh = at ? formatTime(at) : null;
  if (session.state === "AUTH_REQUIRED") {
    return {
      level: "expired",
      label: lastRefresh ? `Données non actualisées depuis ${lastRefresh}` : "Données non actualisées",
      lastRefresh,
    };
  }
  if (session.state !== "READY" || problemWorkflows > 0) {
    const late = problemWorkflows > 0 ? ` · ${problemWorkflows} file${problemWorkflows > 1 ? "s" : ""} à surveiller` : "";
    return {
      level: "stale",
      label: `${lastRefresh ? `Dernière actualisation : ${lastRefresh}` : "En attente de lecture"}${late}`,
      lastRefresh,
    };
  }
  return { level: "ok", label: `Dernière actualisation : ${lastRefresh ?? "—"}`, lastRefresh };
}

export function dashboardSyncStatus(data: Dashboard | undefined): SyncStatus {
  return syncStatus(data?.session, data?.counters?.problem_workflows ?? 0);
}
