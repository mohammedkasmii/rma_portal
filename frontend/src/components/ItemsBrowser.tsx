import { useCallback, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { buildItemsQuery, useAcknowledge, useItems, useWorkflows } from "../api/hooks";
import type { ItemView, WorkStatus } from "../api/types";
import { WORK_STATUSES, WORK_STATUS_LABELS } from "../labels";
import { ItemsTable } from "./ItemsTable";

const PAGE_SIZE = 25;

/** Filters live in the URL so a filtered inbox can be bookmarked, shared and survives reloads. */
export const FILTER_KEYS = ["search", "unread", "kind", "work_status", "portal_status", "procedure", "category", "sort", "order", "page"];

interface Props {
  /** Scope to one workflow, or null for the cross-workflow inbox. */
  workflowKey: string | null;
  caption: string;
}

export function ItemsBrowser({ workflowKey, caption }: Props) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const acknowledge = useAcknowledge();
  const workflows = useWorkflows();
  const [announcement, setAnnouncement] = useState("");

  const query = buildItemsQuery({
    search: params.get("search") ?? undefined,
    unread: params.get("unread") ?? undefined,
    kind: params.get("kind") ?? undefined,
    work_status: params.get("work_status") ?? undefined,
    portal_status: params.get("portal_status") ?? undefined,
    procedure: params.get("procedure") ?? undefined,
    category: params.get("category") ?? undefined,
    workflow: params.getAll("workflow"),
    sort: params.get("sort") ?? undefined,
    order: params.get("order") ?? undefined,
    page: params.get("page") ?? undefined,
    page_size: String(PAGE_SIZE),
  });
  const items = useItems(workflowKey, query);
  const page = items.data;

  const update = useCallback(
    (changes: Record<string, string | null>, resetPage = true) => {
      const next = new URLSearchParams(params);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      if (resetPage) next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const sort = { sort: params.get("sort") ?? "default", descending: params.get("order") !== "asc" };
  const onSort = (key: string) =>
    update({ sort: key, order: sort.sort === key && sort.descending ? "asc" : "desc" });

  // Opening a row is the explicit act that acknowledges its occurrence, for this employee only.
  const onOpen = useCallback(
    async (item: ItemView) => {
      if (item.unread) {
        try {
          await acknowledge.mutateAsync(item.occurrence_id);
          setAnnouncement(`Alerte du dossier ${item.dossier_number} marquée comme lue.`);
        } catch {
          /* opening the dossier must never depend on the acknowledgement */
        }
      }
      void navigate(`/dossiers/${item.dossier_id}?membership=${item.membership_id}`);
    },
    [acknowledge, navigate],
  );

  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1;
  const current = page?.page ?? 1;
  const categories = [...new Set((workflows.data ?? []).filter((w) => w.enabled).map((w) => w.category))];

  return (
    <section aria-label="Dossiers">
      <form className="filters" role="search" onSubmit={(event) => event.preventDefault()}>
        <div className="field">
          <label htmlFor="f-search">Recherche</label>
          <input
            id="f-search"
            type="search"
            placeholder="N° dossier, assuré, immatriculation, garage"
            defaultValue={params.get("search") ?? ""}
            onChange={(event) => update({ search: event.target.value || null })}
          />
        </div>
        <div className="field">
          <label htmlFor="f-alert">Alertes</label>
          <select
            id="f-alert"
            value={params.get("unread") === "true" ? (params.get("kind") ?? "all-unread") : ""}
            onChange={(event) => {
              const v = event.target.value;
              update({ unread: v ? "true" : null, kind: v === "arrival" || v === "changed" ? v : null });
            }}
          >
            <option value="">Toutes</option>
            <option value="all-unread">Non lues</option>
            <option value="arrival">Nouveaux / retours non lus</option>
            <option value="changed">Modifications non lues</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-work">Traitement</label>
          <select
            id="f-work"
            value={params.get("work_status") ?? ""}
            onChange={(event) => update({ work_status: event.target.value || null })}
          >
            <option value="">Tous</option>
            {WORK_STATUSES.map((s: WorkStatus) => (
              <option key={s} value={s}>
                {WORK_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-portal">Statut OmegaFlow</label>
          <select
            id="f-portal"
            value={params.get("portal_status") ?? ""}
            onChange={(event) => update({ portal_status: event.target.value || null })}
          >
            <option value="">Tous</option>
            {(page?.facets.portal_statuses ?? []).map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-proc">Procédure</label>
          <select
            id="f-proc"
            value={params.get("procedure") ?? ""}
            onChange={(event) => update({ procedure: event.target.value || null })}
          >
            <option value="">Toutes</option>
            {(page?.facets.procedures ?? []).map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        {workflowKey === null && (
          <div className="field">
            <label htmlFor="f-cat">Catégorie</label>
            <select
              id="f-cat"
              value={params.get("category") ?? ""}
              onChange={(event) => update({ category: event.target.value || null })}
            >
              <option value="">Toutes</option>
              {categories.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>
        )}
      </form>

      <p role="status" aria-live="polite" className="visually-hidden">
        {announcement}
      </p>
      {items.isError && <p className="error" role="alert">{items.error.message}</p>}
      {items.isLoading && <p>Chargement…</p>}
      {page && (
        <>
          <p className="result-count" aria-live="polite">
            {page.total} dossier{page.total > 1 ? "s" : ""}
          </p>
          <ItemsTable
            caption={caption}
            items={page.items}
            extraColumns={page.columns}
            showWorkflow={workflowKey === null}
            sort={sort}
            onSort={onSort}
            onOpen={(item) => void onOpen(item)}
          />
          <nav className="pagination" aria-label="Pagination">
            <button type="button" disabled={current <= 1} onClick={() => update({ page: String(current - 1) }, false)}>
              ← Précédent
            </button>
            <span>
              Page {current} sur {totalPages}
            </span>
            <button
              type="button"
              disabled={current >= totalPages}
              onClick={() => update({ page: String(current + 1) }, false)}
            >
              Suivant →
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
