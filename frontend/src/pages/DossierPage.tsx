import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAcknowledge, useAddNote, useDossier } from "../api/hooks";
import { AssistantPanel } from "../components/AssistantPanel";
import type { MembershipView } from "../api/types";
import { ClassBadge, RulesBadge } from "../components/Badges";
import { EventList } from "../components/EventList";
import { WorkStatusControl } from "../components/WorkStatusControl";
import { formatDateTime } from "../labels";

const ORIGIN_LABELS = { BASELINE: "Référence initiale", NEW: "Nouvelle arrivée", RETURNED: "Retour dans la file" } as const;

function MembershipPanel({ membership, tabId }: { membership: MembershipView; tabId: string }) {
  return (
    <div role="tabpanel" id={`${tabId}-panel`} aria-labelledby={tabId} tabIndex={0} className="card">
      <p className="badges">
        <ClassBadge value={membership.notification_class} /> <RulesBadge value={membership.rules_status} />
        <span className="badge">{membership.active ? "Actif dans la file" : "Historique — sorti de la file"}</span>
      </p>
      <WorkStatusControl
        key={`${membership.membership_id}-${membership.work_version}`}
        membershipId={membership.membership_id}
        status={membership.work_status}
        version={membership.work_version}
      />
      {membership.work_updated_by && (
        <p className="muted">
          Modifié par {membership.work_updated_by} le {formatDateTime(membership.work_updated_at)}
        </p>
      )}
      <dl className="facts">
        <dt>Première détection</dt>
        <dd>{formatDateTime(membership.first_seen_at)}</dd>
        <dt>Dernière lecture</dt>
        <dd>{formatDateTime(membership.last_seen_at)}</dd>
        <dt>Date clé</dt>
        <dd>{membership.primary_date_raw || "—"}</dd>
        {membership.fields
          .filter((f) => f.value)
          .map((f) => (
            <FactRow key={f.key} label={f.label} value={f.value} />
          ))}
      </dl>
      <h3>Occurrences</h3>
      <ul>
        {membership.occurrences.map((o) => (
          <li key={o.id}>
            #{o.number} — {ORIGIN_LABELS[o.origin]} le {formatDateTime(o.detected_at)}
            {o.unread && <span className="badge badge-unread"> ● non lue</span>}
          </li>
        ))}
      </ul>
      <p>
        <a href={membership.omegaflow_url} target="_blank" rel="noopener noreferrer">
          Ouvrir dans OmegaFlow <span aria-hidden="true">↗</span>
          <span className="visually-hidden"> (nouvel onglet)</span>
        </a>
      </p>
    </div>
  );
}

function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

export function DossierPage() {
  const id = Number(useParams().id);
  const [params] = useSearchParams();
  const { data, isLoading, error } = useDossier(id);
  const acknowledge = useAcknowledge();
  const addNote = useAddNote(id);
  const [selected, setSelected] = useState<number | null>(
    params.get("membership") ? Number(params.get("membership")) : null,
  );
  // Acknowledge only after an explicit open: arriving from a list row or choosing a tab.
  const [opened, setOpened] = useState(params.has("membership"));
  const [note, setNote] = useState("");
  const [noteMembership, setNoteMembership] = useState("");
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
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

  if (isLoading) return <p>Chargement…</p>;
  if (error instanceof ApiError && error.status === 404) return <p role="alert">Ce dossier n’existe pas.</p>;
  if (!data) return <p className="error" role="alert">{error?.message ?? "Erreur de chargement."}</p>;

  const d = data.dossier;
  const select = (index: number) => {
    const target = memberships[(index + memberships.length) % memberships.length];
    setSelected(target.membership_id);
    setOpened(true);
    tabs.current[(index + memberships.length) % memberships.length]?.focus();
  };
  const onKey = (event: KeyboardEvent, index: number) => {
    if (event.key === "ArrowRight") select(index + 1);
    else if (event.key === "ArrowLeft") select(index - 1);
    else if (event.key === "Home") select(0);
    else if (event.key === "End") select(memberships.length - 1);
    else return;
    event.preventDefault();
  };

  const submitNote = (event: FormEvent) => {
    event.preventDefault();
    addNote.mutate(
      { body: note, membershipId: noteMembership ? Number(noteMembership) : null },
      { onSuccess: () => setNote("") },
    );
  };

  return (
    <>
      <h1>
        Dossier {d.dossier_number || d.record_id} — {d.insured_name}
      </h1>
      <section className="card" aria-labelledby="common">
        <h2 id="common">Informations communes</h2>
        <dl className="facts">
          <FactRow label="Immatriculation" value={d.registration || "—"} />
          <FactRow label="Garage" value={d.garage || "—"} />
          <FactRow label="Procédure" value={d.procedure || "—"} />
          <FactRow label="Statut OmegaFlow" value={d.portal_status || "—"} />
          <FactRow label="Ville" value={d.city || "—"} />
          <FactRow label="Observations" value={d.observation_count || "—"} />
          <FactRow label="Première détection" value={formatDateTime(d.first_seen_at)} />
          {d.dates.map((f) => (
            <FactRow key={f.key} label={f.label} value={f.value} />
          ))}
        </dl>
        {d.detail_error && <p className="error">Lecture du détail en échec : {d.detail_error}</p>}
      </section>

      <AssistantPanel scope={{ kind: "dossier", dossierId: id, memberships }} />

      <section aria-labelledby="memberships">
        <h2 id="memberships">Files et statut de traitement</h2>
        {memberships.length === 0 ? (
          <p className="empty">Ce dossier n’est actuellement dans aucune file suivie.</p>
        ) : (
          <>
            <div role="tablist" aria-label="Files du dossier" className="tabs">
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
                    aria-controls={`tab-${m.membership_id}-panel`}
                    tabIndex={isCurrent ? 0 : -1}
                    onClick={() => select(index)}
                    onKeyDown={(event) => onKey(event, index)}
                  >
                    {m.workflow_name}
                    {!m.active && <small> (historique)</small>}
                    {unread && (
                      <span className="badge badge-unread">
                        <span aria-hidden="true">●</span> non lu
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            {current && <MembershipPanel membership={current} tabId={`tab-${current.membership_id}`} />}
          </>
        )}
      </section>

      <section aria-labelledby="timeline">
        <h2 id="timeline">Chronologie</h2>
        <EventList events={data.events} showDossier={false} empty="Aucun événement enregistré." />
      </section>

      <section aria-labelledby="notes">
        <h2 id="notes">Notes partagées</h2>
        <form onSubmit={submitNote} className="card">
          <div className="field">
            <label htmlFor="note-body">Nouvelle note (visible par toute l’équipe)</label>
            <textarea
              id="note-body"
              rows={3}
              maxLength={2000}
              required
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="note-context">Contexte (facultatif)</label>
            <select id="note-context" value={noteMembership} onChange={(event) => setNoteMembership(event.target.value)}>
              <option value="">Dossier entier</option>
              {memberships.map((m) => (
                <option key={m.membership_id} value={m.membership_id}>
                  {m.workflow_name}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" className="primary" disabled={addNote.isPending || !note.trim()}>
            Ajouter la note
          </button>
          <span role="status" aria-live="polite">
            {addNote.isError && <span className="error"> {addNote.error.message}</span>}
            {addNote.isSuccess && " Note ajoutée."}
          </span>
        </form>
        {data.notes.length === 0 ? (
          <p className="empty">Aucune note.</p>
        ) : (
          <ul className="notes">
            {data.notes.map((n) => (
              <li key={n.id} className="card">
                <p>{n.body}</p>
                <p className="muted">
                  {n.author} · {formatDateTime(n.created_at)}
                  {n.workflow_name && ` · ${n.workflow_name}`}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
