import { CircleCheck, ExternalLink, PlugZap, RefreshCw, Unplug } from "lucide-react";
import { useRequestSync, useSyncHealth } from "../../api/hooks";
import { PollBadge } from "../../components/Badges";
import { Button, buttonClass } from "../../components/ui/Button";
import { PageHeader } from "../../components/ui/PageHeader";
import { useToast } from "../../components/ui/Toast";
import { DelayedSkeleton, ErrorState } from "../../components/ui/States";
import { formatDateTime } from "../../labels";

export function AdminSession() {
  const health = useSyncHealth();
  const sync = useRequestSync();
  const toast = useToast();

  if (health.isLoading) {
    return (
      <>
        <PageHeader title="Connexion OmegaFlow" />
        <DelayedSkeleton rows={4} label="Chargement de la session…" />
      </>
    );
  }
  if (!health.data) {
    return (
      <>
        <PageHeader title="Connexion OmegaFlow" />
        <ErrorState message={health.error?.message} onRetry={() => void health.refetch()} />
      </>
    );
  }

  const { session, last_run: lastRun } = health.data;
  const healthy = session.state === "READY";
  const StateIcon = healthy ? CircleCheck : Unplug;
  const refresh = () =>
    sync.mutate(undefined, {
      onSuccess: (result) =>
        toast({ message: result.queued ? "Synchronisation demandée." : "Une demande est déjà en attente." }),
      onError: (error) => toast({ tone: "error", message: error.message }),
    });

  return (
    <>
      <PageHeader
        title="Connexion OmegaFlow"
        description="Session de service utilisée par le portail pour lire les files. Le portail ne voit ni ne stocke jamais le mot de passe OmegaFlow."
      />

      <div className="session-grid">
        <section className="card" aria-labelledby="session-state">
          <div className="session-head">
            <span className={healthy ? "session-icon session-icon-ok" : "session-icon session-icon-danger"} aria-hidden="true">
              <StateIcon size={20} />
            </span>
            <div>
              <h2 id="session-state">{session.label}</h2>
              <p className="key-sub">
                {session.syncing ? "Synchronisation en cours…" : healthy ? "La lecture des files est active." : "Les files ne sont plus actualisées."}
              </p>
            </div>
          </div>
          <dl className="facts facts-single">
            <div className="fact">
              <dt>Dernière lecture</dt>
              <dd>{formatDateTime(session.last_poll_at)}</dd>
            </div>
            <div className="fact">
              <dt>Dernière lecture complète</dt>
              <dd>{formatDateTime(session.last_success_at)}</dd>
            </div>
            {lastRun && (
              <div className="fact">
                <dt>Dernier cycle</dt>
                <dd>
                  <PollBadge status={lastRun.status} /> {lastRun.workflows_complete}/{lastRun.workflows_total} files complètes
                </dd>
              </div>
            )}
            <div className="fact">
              <dt>Demandes en attente</dt>
              <dd>{health.data.pending_sync_requests}</dd>
            </div>
            <div className="fact">
              <dt>Messages en file d’envoi</dt>
              <dd>{health.data.outbox_pending}</dd>
            </div>
            {session.last_error && (
              <div className="fact">
                <dt>Dernière erreur</dt>
                <dd className="error-text">{session.last_error}</dd>
              </div>
            )}
          </dl>
        </section>

        <section className="card" aria-labelledby="reconnect-title">
          <div className="card-body session-actions">
            <div>
              <h2 id="reconnect-title">{healthy ? "Session active" : "Reconnecter la session"}</h2>
              <p className="page-description">
                La session est établie dans le navigateur visible (noVNC), puis réutilisée par le worker de synchronisation.
                {!healthy && " Ouvrez-le, connectez-vous à OmegaFlow, puis actualisez."}
              </p>
            </div>
            <div className="actions-row">
              {session.connect_url ? (
                <a
                  className={buttonClass(healthy ? "secondary" : "primary", "lg")}
                  href={session.connect_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <PlugZap size={16} aria-hidden="true" />
                  {healthy ? "Ouvrir le navigateur de session" : "Se connecter / Reconnecter"}
                  <ExternalLink size={14} aria-hidden="true" />
                  <span className="visually-hidden"> (nouvel onglet)</span>
                </a>
              ) : (
                <p className="key-sub">Le navigateur de session n’est pas configuré sur ce serveur.</p>
              )}
              <Button size="lg" icon={<RefreshCw size={15} aria-hidden="true" />} loading={sync.isPending} onClick={refresh}>
                Actualiser maintenant
              </Button>
            </div>
            <p className="key-sub">Une lecture complète démarre après la demande d’actualisation.</p>
          </div>
        </section>
      </div>
    </>
  );
}
