import { Link } from "react-router-dom";
import { useDashboard } from "../api/hooks";
import { AssistantPanel } from "../components/AssistantPanel";
import { ClassBadge, PollBadge, RulesBadge } from "../components/Badges";
import { EventList } from "../components/EventList";
import { SessionCard } from "../components/SessionCard";
import { WORK_STATUSES, WORK_STATUS_LABELS } from "../labels";

export function Dashboard() {
  const { data, isLoading, isError, error } = useDashboard();

  if (isLoading) return <p>Chargement…</p>;
  if (isError || !data) return <p className="error" role="alert">{error?.message ?? "Erreur de chargement."}</p>;

  const active = data.workflows.filter((w) => w.enabled);
  const { counters } = data;
  return (
    <>
      <h1>Tableau de bord</h1>
      {data.alerts.length > 0 && (
        <section className="banner banner-warning" role="alert" aria-label="Alertes d’administration">
          <strong>⚠ Alertes de synchronisation</strong>
          <EventList events={data.alerts} showDossier={false} />
        </section>
      )}
      <div className="grid">
        <SessionCard session={data.session} lastRun={data.last_run} />
        <section className="card" aria-labelledby="counters">
          <h2 id="counters">Aujourd’hui</h2>
          <ul className="kpis">
            <li>
              <strong>{counters.actionable_new}</strong> nouveau{counters.actionable_new > 1 ? "x" : ""} à traiter
            </li>
            <li>
              <strong>{counters.changed_unread}</strong> modification{counters.changed_unread > 1 ? "s" : ""} non lue
              {counters.changed_unread > 1 ? "s" : ""}
            </li>
            <li>
              <strong>{counters.active_total}</strong> dossiers dans les files
            </li>
            <li className={counters.problem_workflows ? "kpi-problem" : undefined}>
              <strong>{counters.problem_workflows}</strong> workflow{counters.problem_workflows > 1 ? "s" : ""} en anomalie
            </li>
          </ul>
          <p>
            <Link to="/inbox?unread=true">Voir les alertes non lues</Link>
          </p>
        </section>
      </div>

      <AssistantPanel scope={{ kind: "dashboard" }} />

      <section aria-labelledby="load">
        <h2 id="load">Charge par workflow</h2>
        <div className="table-wrap" role="region" aria-label="Charge par workflow" tabIndex={0}>
          <table>
            <caption className="visually-hidden">Dossiers actifs, alertes et statuts de traitement par workflow</caption>
            <thead>
              <tr>
                <th scope="col">Workflow</th>
                <th scope="col">Type</th>
                <th scope="col">Actifs</th>
                <th scope="col">Non lus</th>
                {WORK_STATUSES.map((s) => (
                  <th key={s} scope="col">{WORK_STATUS_LABELS[s]}</th>
                ))}
                <th scope="col">Dernière lecture</th>
              </tr>
            </thead>
            <tbody>
              {active.map((w) => (
                <tr key={w.key}>
                  <th scope="row">
                    <Link to={`/workflows/${w.key}`}>{w.name}</Link> <RulesBadge value={w.rules_status} />
                  </th>
                  <td><ClassBadge value={w.notification_class} /></td>
                  <td>{w.active_count}</td>
                  <td>{w.unread_new + w.unread_changed}</td>
                  {WORK_STATUSES.map((s) => (
                    <td key={s}>{w.by_work_status[s] ?? 0}</td>
                  ))}
                  <td><PollBadge status={w.last_poll_status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="activity">
        <h2 id="activity">Activité récente</h2>
        <EventList events={data.activity} />
      </section>
    </>
  );
}
