import { useState, type FormEvent } from "react";
import { useAdminWorkflows, useCreateUser, useSetUserActive, useSyncHealth, useUpdateWorkflow, useUsers } from "../api/hooks";
import type { NotificationClass, RulesStatus } from "../api/types";
import { useAuth } from "../auth";
import { ClassBadge, PollBadge, RulesBadge } from "../components/Badges";
import { EventList } from "../components/EventList";
import { SessionCard } from "../components/SessionCard";
import { CLASS_LABELS, POLL_LABELS, RULES_LABELS, formatDateTime } from "../labels";

export function AdminSession() {
  const { data, isLoading } = useSyncHealth();
  if (isLoading || !data) return <p>Chargement…</p>;
  return (
    <>
      <h1>Session OmegaFlow</h1>
      <p>
        La session est établie dans le navigateur visible (noVNC) puis réutilisée par le worker de synchronisation. Le
        portail ne voit ni ne stocke jamais le mot de passe OmegaFlow.
      </p>
      <SessionCard session={data.session} lastRun={data.last_run} actions />
      <p className="muted">
        {data.pending_sync_requests} demande(s) de synchronisation en attente · {data.outbox_pending} message(s) en file
        d’envoi
      </p>
    </>
  );
}

export function AdminHealth() {
  const { data, isLoading } = useSyncHealth();
  if (isLoading || !data) return <p>Chargement…</p>;
  return (
    <>
      <h1>Santé des workflows</h1>
      <section aria-labelledby="alerts">
        <h2 id="alerts">Alertes d’administration</h2>
        {data.alerts.length === 0 ? <p className="empty">Aucune alerte.</p> : <EventList events={data.alerts} showDossier={false} />}
      </section>
      <section aria-labelledby="wf-health">
        <h2 id="wf-health">État par workflow</h2>
        <div className="table-wrap" role="region" aria-label="État par workflow" tabIndex={0}>
          <table>
            <caption className="visually-hidden">Dernière lecture de chaque workflow</caption>
            <thead>
              <tr>
                <th scope="col">Workflow</th>
                <th scope="col">Statut</th>
                <th scope="col">Dernière lecture</th>
                <th scope="col">Lignes</th>
                <th scope="col">Nouveaux</th>
                <th scope="col">Erreur</th>
              </tr>
            </thead>
            <tbody>
              {data.workflows.map((w) => (
                <tr key={w.key}>
                  <th scope="row">
                    {w.name} {!w.enabled && <small>(désactivé)</small>} <RulesBadge value={w.rules_status} />
                  </th>
                  <td><PollBadge status={w.last_poll_status} /></td>
                  <td>{formatDateTime(w.last_poll_at)}</td>
                  <td>{w.last_run?.rows_seen ?? "—"}</td>
                  <td>{w.last_run?.created ?? "—"}</td>
                  <td>{w.last_error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section aria-labelledby="runs">
        <h2 id="runs">Derniers cycles</h2>
        <div className="table-wrap" role="region" aria-label="Derniers cycles" tabIndex={0}>
          <table>
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
              {data.recent_runs.map((r) => (
                <tr key={r.id}>
                  <th scope="row">{formatDateTime(r.started_at)}</th>
                  <td>{r.trigger}</td>
                  <td>{POLL_LABELS[r.status] ?? r.status}</td>
                  <td>{r.workflows_complete}/{r.workflows_total}</td>
                  <td>{r.workflows_partial}</td>
                  <td>{r.workflows_auth_required}</td>
                  <td>{r.workflows_failed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

const CLASSES: NotificationClass[] = ["ACTION", "INFORMATIONAL", "SILENT"];
const RULES: RulesStatus[] = ["UNCONFIRMED", "CAPTURE_DERIVED", "CONFIRMED"];

export function AdminWorkflows() {
  const { data, isLoading } = useAdminWorkflows();
  const update = useUpdateWorkflow();
  if (isLoading || !data) return <p>Chargement…</p>;
  return (
    <>
      <h1>Configuration des workflows</h1>
      <p>
        Passer une règle de « À valider sur site » à « Confirmé » ne change pas le moteur d’événements. Un workflow
        « Inactif » n’est pas lu.
      </p>
      <p role="status" aria-live="polite">{update.isError && update.error.message}</p>
      <div className="table-wrap" role="region" aria-label="Configuration" tabIndex={0}>
        <table>
          <caption className="visually-hidden">Activation, règle et type de notification par workflow</caption>
          <thead>
            <tr>
              <th scope="col">Workflow</th>
              <th scope="col">Activé</th>
              <th scope="col">Règle</th>
              <th scope="col">Notification</th>
            </tr>
          </thead>
          <tbody>
            {data.map((w) => (
              <tr key={w.key}>
                <th scope="row">{w.name}</th>
                <td>
                  <label>
                    <input
                      type="checkbox"
                      checked={w.enabled}
                      onChange={(event) => update.mutate({ key: w.key, enabled: event.target.checked })}
                    />{" "}
                    <span className="visually-hidden">Activer {w.name}</span>
                    {w.enabled ? "Oui" : "Non"}
                  </label>
                </td>
                <td>
                  <label className="visually-hidden" htmlFor={`rules-${w.key}`}>Règle de {w.name}</label>
                  <select
                    id={`rules-${w.key}`}
                    value={w.rules_status}
                    onChange={(event) => update.mutate({ key: w.key, rules_status: event.target.value as RulesStatus })}
                  >
                    {RULES.map((r) => (
                      <option key={r} value={r}>{RULES_LABELS[r]}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <label className="visually-hidden" htmlFor={`class-${w.key}`}>Notification de {w.name}</label>
                  <select
                    id={`class-${w.key}`}
                    value={w.notification_class}
                    onChange={(event) =>
                      update.mutate({ key: w.key, notification_class: event.target.value as NotificationClass })
                    }
                  >
                    {CLASSES.map((c) => (
                      <option key={c} value={c}>{CLASS_LABELS[c]}</option>
                    ))}
                  </select>{" "}
                  <ClassBadge value={w.notification_class} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function AdminUsers() {
  const { user: me } = useAuth();
  const { data, isLoading } = useUsers();
  const create = useCreateUser();
  const setActive = useSetUserActive();
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "EMPLOYEE" });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(form, { onSuccess: () => setForm({ username: "", display_name: "", password: "", role: "EMPLOYEE" }) });
  };

  if (isLoading || !data) return <p>Chargement…</p>;
  return (
    <>
      <h1>Utilisateurs</h1>
      <form className="card" onSubmit={submit}>
        <h2>Nouvel utilisateur</h2>
        <div className="field">
          <label htmlFor="u-name">Identifiant</label>
          <input id="u-name" required minLength={3} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="u-display">Nom affiché</label>
          <input id="u-display" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="u-pass">Mot de passe (10 caractères minimum)</label>
          <input id="u-pass" type="password" required autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="u-role">Rôle</label>
          <select id="u-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="EMPLOYEE">Employé</option>
            <option value="ADMIN">Administrateur</option>
          </select>
        </div>
        <button type="submit" className="primary" disabled={create.isPending}>Créer</button>
        <span role="status" aria-live="polite">
          {create.isError && <span className="error"> {create.error.message}</span>}
          {create.isSuccess && " Utilisateur créé."}
        </span>
      </form>
      <p role="status" aria-live="polite">{setActive.isError && <span className="error">{setActive.error.message}</span>}</p>
      <div className="table-wrap" role="region" aria-label="Utilisateurs" tabIndex={0}>
        <table>
          <caption className="visually-hidden">Comptes locaux</caption>
          <thead>
            <tr>
              <th scope="col">Identifiant</th>
              <th scope="col">Nom</th>
              <th scope="col">Rôle</th>
              <th scope="col">État</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>
            {data.map((u) => (
              <tr key={u.id}>
                <th scope="row">{u.username}</th>
                <td>{u.display_name}</td>
                <td>{u.role === "ADMIN" ? "Administrateur" : "Employé"}</td>
                <td>{u.active ? "Actif" : "Désactivé"}</td>
                <td>
                  <button
                    type="button"
                    disabled={u.id === me?.id}
                    title={u.id === me?.id ? "Vous ne pouvez pas désactiver votre propre compte" : undefined}
                    onClick={() => setActive.mutate({ id: u.id, active: !u.active })}
                  >
                    {u.active ? "Désactiver" : "Réactiver"}
                    <span className="visually-hidden"> {u.username}</span>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

