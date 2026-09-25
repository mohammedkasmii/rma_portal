import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useRequestSync, useSyncHealth } from "../../api/hooks";
import type { WorkflowView } from "../../api/types";
import { PollBadge } from "../../components/Badges";
import { EventList } from "../../components/EventList";
import { Button } from "../../components/ui/Button";
import { FilterChip } from "../../components/ui/Filters";
import { PageHeader } from "../../components/ui/PageHeader";
import { DelayedSkeleton, EmptyState, ErrorState } from "../../components/ui/States";
import { useToast } from "../../components/ui/Toast";
import { POLL_LABELS, formatDateTime } from "../../labels";

type Filter = "all" | "problem" | "ok" | "never";

/** Errors first, then partial reads, then never-read queues, then healthy ones. */
const SEVERITY: Record<string, number> = { FAILED: 0, AUTH_REQUIRED: 1, PARTIAL: 2 };
const severity = (w: WorkflowView) => (w.last_poll_status ? (SEVERITY[w.last_poll_status] ?? 4) : 3);
const isProblem = (w: WorkflowView) => severity(w) <= 2;

export function AdminHealth() {
  const health = useSyncHealth();
  const sync = useRequestSync();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");

  const enabled = useMemo(() => (health.data?.workflows ?? []).filter((w) => w.enabled), [health.data]);
  const rows = useMemo(() => {
    const visible = enabled.filter((w) =>
      filter === "all" ? true : filter === "problem" ? isProblem(w) : filter === "never" ? !w.last_poll_status : w.last_poll_status === "COMPLETE",
    );
    return [...visible].sort((a, b) => severity(a) - severity(b) || a.name.localeCompare(b.name, "fr"));
  }, [enabled, filter]);

  if (health.isLoading) {
    return (
      <>
        <PageHeader title="Suivi des lectures" />
        <DelayedSkeleton rows={6} label="Chargement du suivi…" />
      </>
    );
  }
  if (!health.data) {
    return (
      <>
        <PageHeader title="Suivi des lectures" />
        <ErrorState message={health.error?.message} onRetry={() => void health.refetch()} />
      </>
    );
  }

  const { session, last_run: lastRun } = health.data;
  const problems = enabled.filter(isProblem).length;
  const refresh = () =>
    sync.mutate(undefined, {
      onSuccess: (result) => toast({ message: result.queued ? "Synchronisation demandée." : "Une demande est déjà en attente." }),
      onError: (error) => toast({ tone: "error", message: error.message }),
    });

  return (
    <>
      <PageHeader
        title="Suivi des lectures"
        description="État de la dernière lecture de chaque file OmegaFlow."
        actions={
          <Button icon={<RefreshCw size={15} aria-hidden="true" />} loading={sync.isPending} onClick={refresh}>
            Actualiser maintenant
          </Button>
        }
      />

      <div className="summary" role="group" aria-label="Résumé">
        <div>
          <span className="summary-key">Session</span>
          <span className="summary-value">
            <span className={`sync-dot sync-dot-${session.state === "READY" ? "ok" : session.state === "AUTH_REQUIRED" ? "expired" : "stale"}`} aria-hidden="true" />
            {session.label}
          </span>
          <span className="summary-sub">Dernière lecture complète : {formatDateTime(session.last_success_at)}</span>
        </div>
        <div>
          <span className="summary-key">Dernier cycle</span>
          <span className="summary-value">{lastRun ? <PollBadge status={lastRun.status} /> : "—"}</span>
          <span className="summary-sub">
            {lastRun ? `${lastRun.workflows_complete}/${lastRun.workflows_total} files complètes · ${formatDateTime(lastRun.started_at)}` : "Aucun cycle"}
          </span>
        </div>
        <div>
          <span className="summary-key">Files à surveiller</span>
          <span className="summary-value">{problems}</span>
          <span className="summary-sub">sur {enabled.length} files actives</span>
        </div>
        <div>
          <span className="summary-key">Demandes en attente</span>
          <span className="summary-value">{health.data.pending_sync_requests}</span>
          <span className="summary-sub">{health.data.outbox_pending} message(s) en file d’envoi</span>
        </div>
      </div>

      <div className="filter-bar" role="group" aria-label="Filtrer par état">
        <FilterChip selected={filter === "all"} onSelect={() => setFilter("all")} count={enabled.length}>Toutes</FilterChip>
        <FilterChip selected={filter === "problem"} onSelect={() => setFilter("problem")} count={problems}>À surveiller</FilterChip>
        <FilterChip selected={filter === "ok"} onSelect={() => setFilter("ok")} count={enabled.filter((w) => w.last_poll_status === "COMPLETE").length}>Complètes</FilterChip>
        <FilterChip selected={filter === "never"} onSelect={() => setFilter("never")} count={enabled.filter((w) => !w.last_poll_status).length}>Jamais lues</FilterChip>
      </div>

      <div className="card list-card">
        {rows.length === 0 ? (
          <EmptyState title="Aucune file dans cet état" />
        ) : (
          <div className="table-wrap" role="region" aria-label="État par file" tabIndex={0}>
            <table className="admin-table">
              <caption className="visually-hidden">Dernière lecture de chaque file, erreurs en premier</caption>
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Dernière lecture</th>
                  <th scope="col">Lignes lues</th>
                  <th scope="col">Nouveaux</th>
                  <th scope="col">État</th>
                  <th scope="col">Détail</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((w) => (
                  <tr key={w.key} className={isProblem(w) ? "row-problem" : undefined}>
                    <th scope="row">
                      <span className="stack">
                        <span>{w.name}</span>
                        <span className="row-sub">{w.category}</span>
                      </span>
                    </th>
                    <td className="tabular">{formatDateTime(w.last_poll_at)}</td>
                    <td className="tabular">{w.last_run?.rows_seen ?? "—"}</td>
                    <td className="tabular">{w.last_run?.created ?? "—"}</td>
                    <td><PollBadge status={w.last_poll_status} /></td>
                    <td className="error-cell">{w.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <section className="card" aria-labelledby="alerts">
        <div className="card-head">
          <h2 id="alerts">Alertes d’administration</h2>
        </div>
        {health.data.alerts.length === 0 ? <p className="list-empty">Aucune alerte.</p> : <EventList events={health.data.alerts} showDossier={false} />}
      </section>

      <section className="card list-card" aria-labelledby="runs">
        <div className="card-head">
          <h2 id="runs">Derniers cycles</h2>
        </div>
        <div className="table-wrap" role="region" aria-label="Derniers cycles" tabIndex={0}>
          <table className="admin-table">
            <caption className="visually-hidden">Cycles de synchronisation récents</caption>
            <thead>
              <tr>
                <th scope="col">Début</th>
                <th scope="col">Déclencheur</th>
                <th scope="col">Résultat</th>
                <th scope="col">Complets</th>
                <th scope="col">Partiels</th>
                <th scope="col">Reconnexion</th>
                <th scope="col">Échecs</th>
              </tr>
            </thead>
            <tbody>
              {health.data.recent_runs.map((r) => (
                <tr key={r.id}>
                  <th scope="row" className="tabular">{formatDateTime(r.started_at)}</th>
                  <td>{r.trigger}</td>
                  <td>{POLL_LABELS[r.status] ?? r.status}</td>
                  <td className="tabular">{r.workflows_complete}/{r.workflows_total}</td>
                  <td className="tabular">{r.workflows_partial}</td>
                  <td className="tabular">{r.workflows_auth_required}</td>
                  <td className="tabular">{r.workflows_failed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
