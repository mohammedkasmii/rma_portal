import { ChevronRight, Info } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useWorkflowDetail } from "../api/hooks";
import { useAuth } from "../auth";
import { ClassBadge, PollBadge, RulesBadge } from "../components/Badges";
import { EventList } from "../components/EventList";
import { ItemsBrowser } from "../components/ItemsBrowser";
import { PageHeader } from "../components/ui/PageHeader";
import { DelayedSkeleton, EmptyState, ErrorState } from "../components/ui/States";
import { formatDateTime, formatTime } from "../labels";

export function WorkflowPage() {
  const { key = "" } = useParams();
  const { isAdmin } = useAuth();
  const { data, isLoading, error, refetch } = useWorkflowDetail(key);

  if (isLoading) {
    return (
      <>
        <PageHeader title="File" />
        <DelayedSkeleton rows={6} label="Chargement de la file…" />
      </>
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <>
        <PageHeader title="File introuvable" />
        <EmptyState title="Ce workflow n’existe pas.">
          <Link to="/inbox">Retour à À traiter</Link>
        </EmptyState>
      </>
    );
  }
  if (!data) return <ErrorState message={error?.message} onRetry={() => void refetch()} />;

  const { workflow: w } = data;
  const run = w.last_run;
  const refreshed = w.last_success_at ? `Dernière actualisation : ${formatTime(w.last_success_at)}` : "Pas encore lue";
  return (
    <>
      <ItemsBrowser
        key={key}
        workflowKey={key}
        caption={`Dossiers de ${w.name}`}
        statusCounts={{ counts: w.by_work_status, total: w.active_count }}
        filterLabel="Filtrer cette file…"
        emptyTitle="Rien à traiter dans cette file"
        header={(total) => (
          <>
            <PageHeader
              breadcrumb={
                <>
                  <span>Files</span>
                  <ChevronRight size={12} aria-hidden="true" />
                  <span>{w.category}</span>
                </>
              }
              title={w.name}
              count={total}
              description={
                <>
                  {refreshed}
                  {data.filter_label ? ` · ${data.filter_label}` : ""}
                  {!w.enabled && " · Désactivée"}
                </>
              }
              actions={
                <span className="badges">
                  <PollBadge status={w.last_poll_status} />
                </span>
              }
            />
            {w.needs_site_validation && (
              <p className="banner banner-info" role="note">
                <Info size={18} aria-hidden="true" />
                <span>
                  Cette file suit une règle provisoire : une arrivée dans la file est l’événement à signaler. Elle sera
                  confirmée avec l’agence.
                </span>
              </p>
            )}
          </>
        )}
      />

      {isAdmin && (
        <details className="card disclosure">
          <summary>Détails de lecture (administration)</summary>
          <dl className="facts facts-single">
            <div className="fact">
              <dt>Configuration</dt>
              <dd className="badges"><ClassBadge value={w.notification_class} /> <RulesBadge value={w.rules_status} /></dd>
            </div>
            <div className="fact">
              <dt>Référence initiale</dt>
              <dd>{w.baseline_completed_at ? formatDateTime(w.baseline_completed_at) : "Pas encore établie — aucune alerte tant qu’elle n’est pas faite"}</dd>
            </div>
            <div className="fact">
              <dt>Dernière lecture</dt>
              <dd>{formatDateTime(w.last_poll_at)}</dd>
            </div>
            <div className="fact">
              <dt>Dernière lecture complète</dt>
              <dd>{formatDateTime(w.last_success_at)}</dd>
            </div>
            {data.primary_date_labels.length > 0 && (
              <div className="fact">
                <dt>Date clé</dt>
                <dd>{data.primary_date_labels.join(" › ")}, sinon date de détection</dd>
              </div>
            )}
            {run && (
              <div className="fact">
                <dt>Dernier cycle</dt>
                <dd>
                  {run.rows_seen} ligne(s) · {run.created} nouveau(x) · {run.returned} retour(s) · {run.changed} modif. · {run.left} sorti(s)
                  {run.details_failed > 0 && ` · ${run.details_failed} détail(s) en échec`}
                </dd>
              </div>
            )}
          </dl>
          {w.last_error && <p className="error-text disclosure-error">Dernière erreur : {w.last_error}</p>}
        </details>
      )}

      <section className="card" aria-labelledby="wf-activity">
        <div className="card-head">
          <h2 id="wf-activity">Activité de la file</h2>
        </div>
        <EventList events={data.recent_events} />
      </section>
    </>
  );
}
