import { useRequestSync } from "../api/hooks";
import type { SessionHealth, SyncRun } from "../api/types";
import { formatDateTime } from "../labels";
import { PollBadge } from "./Badges";

interface Props {
  session: SessionHealth;
  lastRun: SyncRun | null | undefined;
  actions?: boolean;
}

export function SessionCard({ session, lastRun, actions = false }: Props) {
  const sync = useRequestSync();
  const healthy = session.state === "READY";
  return (
    <section className={`card session session-${session.state.toLowerCase()}`} aria-labelledby="session-title">
      <h2 id="session-title">Session OmegaFlow</h2>
      <p>
        <span aria-hidden="true">{healthy ? "✓" : "⚠"}</span> <strong>{session.label}</strong>
        {session.syncing && <span className="badge"> Synchronisation en cours…</span>}
      </p>
      <dl className="facts">
        <dt>Dernière lecture</dt>
        <dd>{formatDateTime(session.last_poll_at)}</dd>
        <dt>Dernière lecture complète</dt>
        <dd>{formatDateTime(session.last_success_at)}</dd>
        {lastRun && (
          <>
            <dt>Dernier cycle</dt>
            <dd>
              <PollBadge status={lastRun.status} /> {lastRun.workflows_complete}/{lastRun.workflows_total} complets
            </dd>
          </>
        )}
      </dl>
      {session.last_error && <p className="error">Dernière erreur : {session.last_error}</p>}
      {actions && (
        <div className="actions">
          {session.connect_url && (
            <a className="button primary" href={session.connect_url} target="_blank" rel="noopener noreferrer">
              {healthy ? "Ouvrir le navigateur de session" : "Se connecter / Reconnecter"}
            </a>
          )}
          <button type="button" disabled={sync.isPending} onClick={() => sync.mutate()}>
            Actualiser maintenant
          </button>
          <span role="status" aria-live="polite">
            {sync.data && (sync.data.queued ? "Synchronisation demandée." : "Une demande est déjà en attente.")}
            {sync.isError && sync.error.message}
          </span>
        </div>
      )}
    </section>
  );
}
