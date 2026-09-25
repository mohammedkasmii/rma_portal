import { ArrowRight, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { buildItemsQuery, useDashboard, useItems } from "../api/hooks";
import type { ItemView, WorkflowView } from "../api/types";
import { useAuth } from "../auth";
import { AssistantPanel } from "../components/AssistantPanel";
import { EventList } from "../components/EventList";
import { ChangeMarker, OmegaFlowStatusTag } from "../components/ui/Badges";
import { buttonClass } from "../components/ui/Button";
import { PageHeader } from "../components/ui/PageHeader";
import { DelayedSkeleton, EmptyState, ErrorState } from "../components/ui/States";
import { useOpenItem } from "../hooks/useOpenItem";
import { formatWhen } from "../labels";

const dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });
const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const plural = (n: number, one: string, many: string) => (n > 1 ? many : one);
const actionable = (w: WorkflowView) => w.unread_new + w.unread_changed;

/** Files whose last reading did not complete: shown to everyone as a neutral "data may be stale" notice. */
function staleQueues(workflows: WorkflowView[]): string[] {
  return workflows
    .filter((w) => w.enabled && w.last_poll_status && w.last_poll_status !== "COMPLETE")
    .map((w) => w.name);
}

interface TileProps {
  label: string;
  value: number;
  hint: string;
  to: string;
  tone: "accent" | "new" | "mod" | "prog";
}

function Tile({ label, value, hint, to, tone }: TileProps) {
  return (
    <Link to={to} className={`tile tile-${tone}`} aria-label={`${label} : ${value}`}>
      <span className="tile-label">
        {label}
        <ArrowRight size={15} aria-hidden="true" />
      </span>
      <span className="tile-value">{value}</span>
      <span className="tile-hint">{hint}</span>
    </Link>
  );
}

function UnreadRow({ item, onOpen }: { item: ItemView; onOpen: (item: ItemView) => void }) {
  return (
    <li>
      <button type="button" className="unread-row" onClick={() => onOpen(item)}>
        <span className="unread-dot" aria-hidden="true" />
        <span className="unread-marker">
          <ChangeMarker kind={item.unread_kind} />
        </span>
        <span className="unread-text">
          <span className="unread-main">
            <span className="mono">{item.dossier_number || item.record_id}</span> · {item.insured_name}
            <span className="muted"> · {item.workflow_name}</span>
          </span>
          <span className="unread-sub">
            Statut OmegaFlow : <OmegaFlowStatusTag value={item.portal_status} />
          </span>
        </span>
        <time className="unread-when" dateTime={item.detected_at}>
          {formatWhen(item.detected_at)}
        </time>
      </button>
    </li>
  );
}

export function Dashboard() {
  const { user, isAdmin } = useAuth();
  const dashboard = useDashboard();
  const unread = useItems(null, buildItemsQuery({ unread: "true", page_size: "8" }));
  const open = useOpenItem();

  if (dashboard.isLoading) {
    return (
      <>
        <PageHeader title="Accueil" />
        <DelayedSkeleton rows={5} label="Chargement de l’accueil…" />
      </>
    );
  }
  if (dashboard.isError || !dashboard.data) {
    return (
      <>
        <PageHeader title="Accueil" />
        <ErrorState message={dashboard.error?.message} onRetry={() => void dashboard.refetch()} />
      </>
    );
  }

  const data = dashboard.data;
  const { counters } = data;
  const todo = counters.by_work_status?.TO_DO ?? 0;
  const inProgress = counters.by_work_status?.IN_PROGRESS ?? 0;
  const firstName = (user?.display_name ?? "").split(" ")[0];
  const stale = staleQueues(data.workflows);
  const busiest = data.workflows
    .filter((w) => w.enabled && actionable(w) > 0)
    .sort((a, b) => actionable(b) - actionable(a) || a.name.localeCompare(b.name, "fr"))
    .slice(0, 5);
  const total = unread.data?.total ?? 0;

  return (
    <>
      <PageHeader
        title={firstName ? `Bonjour ${firstName}` : "Accueil"}
        description={`${capitalize(dayFmt.format(new Date()))} · ${
          todo > 0 ? `${todo} ${plural(todo, "dossier demande", "dossiers demandent")} une action` : "Rien à traiter pour le moment"
        }`}
      />

      {stale.length > 0 && (
        <div role="status" className="banner banner-warn">
          <TriangleAlert size={18} aria-hidden="true" />
          <p>
            <strong>Certaines données ne sont pas à jour.</strong> {plural(stale.length, "La file", "Les files")}{" "}
            {stale.slice(0, 3).join(", ")}
            {stale.length > 3 ? ` et ${stale.length - 3} autre${stale.length - 3 > 1 ? "s" : ""}` : ""}{" "}
            {plural(stale.length, "n’a pas été lue", "n’ont pas été lues")} complètement à la dernière actualisation.
            Les informations affichées peuvent être incomplètes.
          </p>
        </div>
      )}
      {isAdmin && data.alerts.length > 0 && (
        <section className="banner banner-warn" role="alert" aria-label="Alertes d’administration">
          <TriangleAlert size={18} aria-hidden="true" />
          <div className="banner-events">
            <strong>Alertes de synchronisation</strong>
            <EventList events={data.alerts} showDossier={false} />
          </div>
        </section>
      )}

      <div className="tiles">
        <Tile label="À traiter" value={todo} hint="Statut de traitement « À traiter »" to="/inbox?work_status=TO_DO" tone="accent" />
        <Tile
          label="Nouveaux"
          value={counters.actionable_new}
          hint="Nouveaux dossiers non lus"
          to="/inbox?unread=true&kind=arrival"
          tone="new"
        />
        <Tile
          label="Modifications"
          value={counters.changed_unread}
          hint="Modifications non lues"
          to="/inbox?unread=true&kind=changed"
          tone="mod"
        />
        <Tile label="En cours" value={inProgress} hint="Statut de traitement « En cours »" to="/inbox?work_status=IN_PROGRESS" tone="prog" />
      </div>

      <div className="dash-grid">
        <section className="card" aria-labelledby="unread-title">
          <div className="card-head">
            <h2 id="unread-title">Changements non lus</h2>
            <span className="count count-neutral">{total}</span>
            <span className="spacer" />
            <Link to="/inbox?unread=true" className={buttonClass("secondary")}>
              Voir dans À traiter
            </Link>
          </div>
          {unread.isLoading && <DelayedSkeleton rows={4} label="Chargement des changements non lus…" />}
          {unread.isError && <ErrorState title="Changements non lus indisponibles" message={unread.error.message} onRetry={() => void unread.refetch()} />}
          {unread.data && unread.data.items.length === 0 && (
            <EmptyState title="Aucun changement non lu">Les nouveaux dossiers et modifications apparaîtront ici à la prochaine actualisation.</EmptyState>
          )}
          {unread.data && unread.data.items.length > 0 && (
            <ul>
              {unread.data.items.map((item) => (
                <UnreadRow key={item.occurrence_id} item={item} onOpen={(row) => void open(row)} />
              ))}
            </ul>
          )}
        </section>

        <div className="dash-side">
          <section className="card" aria-labelledby="busiest-title">
            <div className="card-head">
              <h2 id="busiest-title">Files avec le plus d’actions</h2>
            </div>
            {busiest.length === 0 ? (
              <p className="list-empty">Aucune file n’a d’alerte non lue.</p>
            ) : (
              <ul>
                {busiest.map((w) => (
                  <li key={w.key}>
                    <Link to={`/workflows/${w.key}`} className="busy-row">
                      <span className="busy-name">{w.name}</span>
                      <span className="muted busy-cat">{w.category}</span>
                      <span className="busy-count" aria-label={`${actionable(w)} non lus`}>{actionable(w)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card" aria-labelledby="activity-title">
            <div className="card-head">
              <h2 id="activity-title">Activité récente</h2>
            </div>
            <EventList events={data.activity.slice(0, 6)} />
          </section>
        </div>
      </div>

      <AssistantPanel scope={{ kind: "dashboard" }} />
    </>
  );
}
