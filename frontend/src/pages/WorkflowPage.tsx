import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useWorkflowDetail } from "../api/hooks";
import { ClassBadge, PollBadge, RulesBadge } from "../components/Badges";
import { EventList } from "../components/EventList";
import { ItemsBrowser } from "../components/ItemsBrowser";
import { formatDateTime } from "../labels";

export function WorkflowPage() {
  const { key = "" } = useParams();
  const { data, isLoading, error } = useWorkflowDetail(key);

  if (isLoading) return <p>Chargement…</p>;
  if (error instanceof ApiError && error.status === 404) return <p role="alert">Ce workflow n’existe pas.</p>;
  if (!data) return <p className="error" role="alert">{error?.message ?? "Erreur de chargement."}</p>;

  const { workflow: w } = data;
  const run = w.last_run;
  return (
    <>
      <h1>{w.name}</h1>
      <p className="badges">
        <ClassBadge value={w.notification_class} /> <RulesBadge value={w.rules_status} />
        {!w.enabled && <span className="badge">Désactivé</span>}
        <PollBadge status={w.last_poll_status} />
      </p>
      {w.needs_site_validation && (
        <p className="banner banner-info">
          Cette file suit la règle prudente issue des captures : une arrivée dans la file est l’événement. Elle reste à
          valider sur site avec l’agence.
        </p>
      )}
      <section className="card" aria-labelledby="health">
        <h2 id="health">Santé de la file</h2>
        <dl className="facts">
          <dt>Référence initiale (baseline)</dt>
          <dd>{w.baseline_completed_at ? formatDateTime(w.baseline_completed_at) : "pas encore établie — aucune alerte tant qu’elle n’est pas faite"}</dd>
          <dt>Dernière lecture</dt>
          <dd>{formatDateTime(w.last_poll_at)}</dd>
          <dt>Dernière lecture complète</dt>
          <dd>{formatDateTime(w.last_success_at)}</dd>
          {data.filter_label && (
            <>
              <dt>Filtre de procédure</dt>
              <dd>{data.filter_label}</dd>
            </>
          )}
          {data.primary_date_labels.length > 0 && (
            <>
              <dt>Date affichée</dt>
              <dd>{data.primary_date_labels.join(" › ")}, sinon date de détection</dd>
            </>
          )}
          {run && (
            <>
              <dt>Dernier cycle</dt>
              <dd>
                {run.rows_seen} ligne(s) · {run.created} nouveau(x) · {run.returned} retour(s) · {run.changed} modif. ·{" "}
                {run.left} sorti(s)
                {run.details_failed > 0 && ` · ${run.details_failed} détail(s) en échec`}
              </dd>
            </>
          )}
        </dl>
        {w.last_error && <p className="error">Dernière erreur : {w.last_error}</p>}
      </section>
      <ItemsBrowser key={key} workflowKey={key} caption={`Dossiers de ${w.name}`} />
      <section aria-labelledby="wf-activity">
        <h2 id="wf-activity">Activité de la file</h2>
        <EventList events={data.recent_events} />
      </section>
    </>
  );
}
