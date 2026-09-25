import { ArrowLeft, CalendarClock, ExternalLink, MessageSquarePlus, RefreshCcw } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAcknowledge, useDossier } from "../api/hooks";
import type { EventOut, MembershipView } from "../api/types";
import { AssistantPanel } from "../components/AssistantPanel";
import { EventList } from "../components/EventList";
import { NotesPanel } from "../components/NotesPanel";
import { ChangeMarker, OmegaFlowStatusTag } from "../components/ui/Badges";
import { Button, buttonClass } from "../components/ui/Button";
import { DelayedSkeleton, EmptyState, ErrorState } from "../components/ui/States";
import { TreatmentStatusControl } from "../components/ui/TreatmentStatusControl";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { EVENT_LABELS, WORK_STATUS_LABELS, formatDateTime, formatTime, formatWhen } from "../labels";

const ORIGIN_LABELS = { BASELINE: "Référence initiale", NEW: "Nouvelle arrivée", RETURNED: "Retour dans la file" } as const;
const ORIGIN_KINDS = { BASELINE: "WORKFLOW_ITEM_CHANGED", NEW: "WORKFLOW_ITEM_NEW", RETURNED: "WORKFLOW_ITEM_RETURNED" } as const;

/** Kind of the alert still unread on this membership: the event of that occurrence, else its origin. */
function unreadKind(membership: MembershipView | undefined, events: EventOut[]): string | null {
  const occurrence = membership?.occurrences.find((o) => o.unread);
  if (!occurrence) return null;
  const event = events.find((e) => e.occurrence_id === occurrence.id);
  return event?.kind ?? ORIGIN_KINDS[occurrence.origin];
}

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value}</dd>
    </div>
  );
}

export function DossierPage() {
  const id = Number(useParams().id);
  const [params] = useSearchParams();
  const { data, isLoading, error, refetch } = useDossier(id);
  const acknowledge = useAcknowledge();
  const mobile = useMediaQuery("(max-width: 767px)");
  const [selected, setSelected] = useState<number | null>(
    params.get("membership") ? Number(params.get("membership")) : null,
  );
  // Acknowledge only after an explicit open: arriving from a list row or choosing a file tab.
  const [opened, setOpened] = useState(params.has("membership"));
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const statusRef = useRef<HTMLDivElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const acked = useRef(new Set<number>());

  const memberships = data?.memberships ?? [];
  const current = memberships.find((m) => m.membership_id === selected) ?? memberships[0];

  useEffect(() => {
    if (!opened || !current) return;
    for (const occurrence of current.occurrences) {
      if (occurrence.unread && !acked.current.has(occurrence.id)) {
        acked.current.add(occurrence.id);
        acknowledge.mutate(occurrence.id);
      }
    }
  }, [opened, current, acknowledge]);

  if (isLoading) return <DelayedSkeleton rows={6} label="Chargement du dossier…" />;
  if (error instanceof ApiError && error.status === 404) {
    return <EmptyState title="Ce dossier n’existe pas.">Il a peut-être été retiré de la base du portail.</EmptyState>;
  }
  if (!data) return <ErrorState message={error?.message} onRetry={() => void refetch()} />;

  const d = data.dossier;
  const number = d.dossier_number || d.record_id;
  const select = (index: number) => {
    const wrapped = (index + memberships.length) % memberships.length;
    setSelected(memberships[wrapped].membership_id);
    setOpened(true);
    tabs.current[wrapped]?.focus();
  };
  const onKey = (event: KeyboardEvent, index: number) => {
    if (event.key === "ArrowRight") select(index + 1);
    else if (event.key === "ArrowLeft") select(index - 1);
    else if (event.key === "Home") select(0);
    else if (event.key === "End") select(memberships.length - 1);
    else return;
    event.preventDefault();
  };

  const kind = unreadKind(current, data.events);
  const currentUnread = current?.occurrences.find((o) => o.unread);
  const dossierEvents = current ? data.events.filter((e) => e.membership_id === current.membership_id || e.membership_id === null) : data.events;
  const changes = dossierEvents.filter((e) => e.changed_labels.length > 0 || e.kind !== "WORKFLOW_POLL_PARTIAL").slice(0, 3);
  const omegaflow = current?.omegaflow_url;

  return (
    <>
      <nav className="breadcrumb" aria-label="Fil d’Ariane">
        <Link to="/inbox">
          <ArrowLeft size={13} aria-hidden="true" /> À traiter
        </Link>
      </nav>

      <div className="card dossier-head">
        <div className="dossier-title-row">
          <div className="dossier-identity">
            <div className="dossier-number-row">
              <h1 className="dossier-number mono">{number}</h1>
              <ChangeMarker kind={kind} />
              {currentUnread && <span className="muted meta-inline">non lue · {formatWhen(currentUnread.detected_at)}</span>}
            </div>
            <p className="dossier-sub">
              {d.insured_name}
              {d.registration && <span className="mono"> · {d.registration}</span>}
            </p>
          </div>
          {!mobile && omegaflow && (
            <a className={buttonClass("primary", "lg")} href={omegaflow} target="_blank" rel="noopener noreferrer">
              Ouvrir dans OmegaFlow
              <ExternalLink size={15} aria-hidden="true" />
              <span className="visually-hidden"> (nouvel onglet)</span>
            </a>
          )}
        </div>

        {d.detail_error && (
          <p className="banner banner-warn" role="alert">
            <span>Lecture du détail en échec : {d.detail_error}</span>
          </p>
        )}

        {memberships.length === 0 ? (
          <EmptyState title="Ce dossier n’est actuellement dans aucune file suivie." />
        ) : (
          <>
            <div role="tablist" aria-label="Files du dossier" className="file-tabs">
              {memberships.map((m, index) => {
                const isCurrent = m.membership_id === current?.membership_id;
                const unread = m.occurrences.some((o) => o.unread);
                return (
                  <button
                    key={m.membership_id}
                    ref={(node) => {
                      tabs.current[index] = node;
                    }}
                    role="tab"
                    id={`tab-${m.membership_id}`}
                    type="button"
                    aria-selected={isCurrent}
                    aria-controls="file-panel"
                    tabIndex={isCurrent ? 0 : -1}
                    className="file-tab"
                    onClick={() => select(index)}
                    onKeyDown={(event) => onKey(event, index)}
                  >
                    {m.workflow_name}
                    {!m.active && <small> (historique)</small>}
                    {unread && <span className="marker marker-new">non lu</span>}
                  </button>
                );
              })}
            </div>

            {current && (
              <div id="file-panel" role="tabpanel" aria-labelledby={`tab-${current.membership_id}`}>
                <div className="key-facts">
                  <div>
                    <p className="key-label">
                      <CalendarClock size={13} aria-hidden="true" /> Date clé
                    </p>
                    <p className="key-value">{current.primary_date_raw || "—"}</p>
                  </div>
                  <div>
                    <p className="key-label">File actuelle</p>
                    <p className="key-value">{current.workflow_name}</p>
                    <p className="key-sub">
                      {current.workflow_category} · {current.active ? `depuis le ${formatDateTime(current.first_seen_at)}` : "historique — sorti de la file"}
                    </p>
                  </div>
                  <div>
                    <p className="key-label">Statut OmegaFlow</p>
                    <p className="key-value"><OmegaFlowStatusTag value={d.portal_status} /></p>
                    <p className="key-sub">Lu dans OmegaFlow à {formatTime(current.last_seen_at)}</p>
                  </div>
                </div>

                <div className="status-row" ref={statusRef}>
                  <div className="status-row-label">
                    Statut de traitement
                    <span className="key-sub">Partagé avec l’équipe</span>
                  </div>
                  <TreatmentStatusControl
                    key={`${current.membership_id}-${current.work_version}`}
                    variant="segmented"
                    membershipId={current.membership_id}
                    status={current.work_status}
                    version={current.work_version}
                  />
                  {current.work_updated_by && (
                    <p className="key-sub">
                      Passé à « {WORK_STATUS_LABELS[current.work_status]} » par {current.work_updated_by} ·{" "}
                      {formatWhen(current.work_updated_at)}
                    </p>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <AssistantPanel scope={{ kind: "dossier", dossierId: id, memberships }} />

      <div className="dossier-grid">
        <div className="dossier-main">
          {changes.length > 0 && (
            <section className="card" aria-labelledby="changed-title">
              <div className="card-head">
                <h2 id="changed-title">Ce qui a changé</h2>
                <span className="meta">{formatWhen(changes[0].detected_at)}</span>
              </div>
              <ul className="change-list">
                {changes.map((event) => (
                  <li key={event.id}>
                    <strong>{EVENT_LABELS[event.kind] ?? event.kind}</strong>
                    {event.changed_labels.length > 0 && <span> · {event.changed_labels.join(", ")}</span>}
                    {event.workflow_name && <span className="muted"> · {event.workflow_name}</span>}
                    <time className="muted" dateTime={event.detected_at}> · {formatWhen(event.detected_at)}</time>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="card" aria-labelledby="info-title">
            <div className="card-head">
              <h2 id="info-title">Informations du dossier</h2>
              <span className="meta">Source : OmegaFlow</span>
            </div>
            <dl className="facts">
              <Fact label="Immatriculation" value={d.registration || "—"} mono />
              <Fact label="Garage" value={d.garage || "—"} />
              <Fact label="Procédure" value={d.procedure || "—"} />
              <Fact label="Statut OmegaFlow" value={d.portal_status || "—"} />
              <Fact label="Ville" value={d.city || "—"} />
              <Fact label="Observations" value={d.observation_count || "—"} />
              <Fact label="Première détection" value={formatDateTime(d.first_seen_at)} />
              {d.dates.map((f) => (
                <Fact key={f.key} label={f.label} value={f.value} />
              ))}
              {current?.fields
                .filter((f) => f.value)
                .map((f) => (
                  <Fact key={`m-${f.key}`} label={f.label} value={f.value} />
                ))}
            </dl>
            {current && (
              <details className="card-details">
                <summary>Historique des arrivées dans cette file</summary>
                <ul>
                  {current.occurrences.map((o) => (
                    <li key={o.id}>
                      #{o.number} — {ORIGIN_LABELS[o.origin]} le {formatDateTime(o.detected_at)}
                      {o.unread && <span className="marker marker-new"> non lue</span>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </section>

          <section className="card" aria-labelledby="timeline">
            <div className="card-head">
              <h2 id="timeline">Chronologie</h2>
            </div>
            <EventList events={data.events} showDossier={false} empty="Aucun événement enregistré." />
          </section>
        </div>

        <div className="dossier-side">
          <NotesPanel dossierId={id} notes={data.notes} memberships={memberships} textareaRef={noteRef} />
        </div>
      </div>

      {mobile && current && (
        <div className="action-bar">
          {omegaflow && (
            <a className={buttonClass("primary", "lg")} href={omegaflow} target="_blank" rel="noopener noreferrer">
              Ouvrir dans OmegaFlow
              <ExternalLink size={16} aria-hidden="true" />
              <span className="visually-hidden"> (nouvel onglet)</span>
            </a>
          )}
          <div className="action-bar-row">
            <Button
              size="lg"
              icon={<RefreshCcw size={15} aria-hidden="true" />}
              onClick={() => {
                statusRef.current?.scrollIntoView({ block: "center" });
                statusRef.current?.querySelector<HTMLButtonElement>("[role=radio][tabindex='0']")?.focus();
              }}
            >
              Changer le statut
            </Button>
            <Button
              size="lg"
              icon={<MessageSquarePlus size={15} aria-hidden="true" />}
              onClick={() => {
                noteRef.current?.scrollIntoView({ block: "center" });
                noteRef.current?.focus();
              }}
            >
              Ajouter une note
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
