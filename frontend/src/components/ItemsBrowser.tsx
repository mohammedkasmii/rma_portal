import { ChevronLeft, ChevronRight, ListFilter, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { buildItemsQuery, useItems, useWorkflows } from "../api/hooks";
import type { ItemView } from "../api/types";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { useOpenItem } from "../hooks/useOpenItem";
import { WORK_STATUSES, WORK_STATUS_LABELS } from "../labels";
import { DossierCards } from "./DossierCard";
import { DossierPreviewPanel } from "./DossierPreviewPanel";
import { ItemsTable } from "./ItemsTable";
import { StatusTabs } from "./StatusTabs";
import { Button } from "./ui/Button";
import { FacetSelect, FilterChip } from "./ui/Filters";
import { DelayedSkeleton, EmptyState, ErrorState } from "./ui/States";

const PAGE_SIZE = 25;

/** Filters live in the URL so a filtered list can be bookmarked, shared and survives reloads. */
export const FILTER_KEYS = ["search", "unread", "kind", "work_status", "portal_status", "procedure", "category", "workflow", "sort", "order", "page"];
const FACET_KEYS = ["search", "unread", "kind", "work_status", "portal_status", "procedure", "category", "workflow"];

type Quick = "all" | "unread" | "new" | "changed";

interface Props {
  /** Scope to one workflow, or null for the cross-workflow inbox. */
  workflowKey: string | null;
  caption: string;
  /** Queue pages show the API's treatment-status counts as tabs. */
  statusCounts?: { counts: Record<string, number>; total: number };
  /** Placeholder of the list filter, e.g. "Filtrer cette file…". */
  filterLabel?: string;
  emptyTitle?: string;
  /** Page header rendered above the tabs and filters; receives the filtered total once known. */
  header?: (total: number | undefined) => ReactNode;
}

export function ItemsBrowser({ workflowKey, caption, statusCounts, filterLabel = "Filtrer cette liste…", emptyTitle = "Rien à traiter ici", header }: Props) {
  const [params, setParams] = useSearchParams();
  const workflows = useWorkflows();
  const mobile = useMediaQuery("(max-width: 767px)");
  const wide = useMediaQuery("(min-width: 1280px)");
  const [announcement, setAnnouncement] = useState("");
  const [facetsOpen, setFacetsOpen] = useState(false);
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [searchText, setSearchText] = useState(params.get("search") ?? "");
  const debouncedSearch = useDebouncedValue(searchText.trim(), 300);

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
        next.delete(key);
        if (value) next.set(key, value);
      }
      if (resetPage) next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  // The list filter asks the server once typing pauses, instead of after every keystroke.
  const urlSearch = params.get("search") ?? "";
  useEffect(() => {
    if (debouncedSearch !== urlSearch) update({ search: debouncedSearch || null });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a settled search should touch the URL
  }, [debouncedSearch]);

  const sort = { sort: params.get("sort") ?? "default", descending: params.get("order") !== "asc" };
  const onSort = (key: string) => update({ sort: key, order: sort.sort === key && sort.descending ? "asc" : "desc" });

  const openItem = useOpenItem(
    useCallback((item: ItemView) => setAnnouncement(`Alerte du dossier ${item.dossier_number} marquée comme lue.`), []),
  );

  const quick: Quick =
    params.get("unread") !== "true" ? "all" : params.get("kind") === "arrival" ? "new" : params.get("kind") === "changed" ? "changed" : "unread";
  const setQuick = (value: Quick) =>
    update({ unread: value === "all" ? null : "true", kind: value === "new" ? "arrival" : value === "changed" ? "changed" : null });

  const scope = (workflows.data ?? []).filter((w) => w.enabled && (workflowKey === null || w.key === workflowKey));
  const unreadNew = scope.reduce((sum, w) => sum + w.unread_new, 0);
  const unreadChanged = scope.reduce((sum, w) => sum + w.unread_changed, 0);
  const categories = [...new Set((workflows.data ?? []).filter((w) => w.enabled).map((w) => w.category))];
  const activeFilters = FACET_KEYS.filter((key) => params.has(key));
  const clearFilters = () => {
    setSearchText("");
    update(Object.fromEntries(FACET_KEYS.map((key) => [key, null])));
  };

  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1;
  const current = page?.page ?? 1;
  const from = page && page.total > 0 ? (current - 1) * page.page_size + 1 : 0;
  const to = page ? Math.min(page.total, (current - 1) * page.page_size + page.items.length) : 0;
  const previewed = previewId && page ? (page.items.find((item) => item.membership_id === previewId) ?? null) : null;
  const canPreview = wide && workflowKey !== null;
  const panelId = "items-panel";

  const body = (() => {
    if (items.isLoading) return <DelayedSkeleton rows={8} label="Chargement des dossiers…" />;
    if (items.isError && !page) {
      return <ErrorState title="Impossible de charger la liste" message={items.error.message} onRetry={() => void items.refetch()} />;
    }
    if (!page) return null;
    if (page.items.length === 0) {
      return activeFilters.length > 0 ? (
        <EmptyState title="Aucun dossier ne correspond à ces critères">
          <Button onClick={clearFilters}>Effacer les filtres</Button>
        </EmptyState>
      ) : (
        <EmptyState title={emptyTitle}>Les nouveaux dossiers apparaîtront ici à la prochaine actualisation.</EmptyState>
      );
    }
    return mobile ? (
      <DossierCards items={page.items} caption={caption} showWorkflow={workflowKey === null} onOpen={(item) => void openItem(item)} />
    ) : (
      <ItemsTable
        caption={caption}
        items={page.items}
        extraColumns={page.columns}
        showWorkflow={workflowKey === null}
        sort={sort}
        onSort={onSort}
        onOpen={(item) => void openItem(item)}
        onPreview={canPreview ? (item) => setPreviewId(previewId === item.membership_id ? null : item.membership_id) : undefined}
        previewedId={canPreview ? previewId : null}
      />
    );
  })();

  return (
    <section aria-label="Dossiers" className="browser">
      {header?.(page?.total)}
      {statusCounts && (
        <StatusTabs
          value={params.get("work_status") ?? ""}
          onChange={(value) => update({ work_status: value || null })}
          counts={statusCounts.counts}
          total={statusCounts.total}
          panelId={panelId}
        />
      )}

      <div className="filter-bar" role="search">
        <div className="filter-input">
          <ListFilter size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Filtrer la liste"
            placeholder={filterLabel}
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </div>
        <span className="filter-sep" aria-hidden="true" />
        <div className="quick-chips" role="group" aria-label="Vues rapides">
          <FilterChip selected={quick === "all"} onSelect={() => setQuick("all")}>Tous</FilterChip>
          <FilterChip selected={quick === "unread"} onSelect={() => setQuick("unread")} count={unreadNew + unreadChanged}>Non lus</FilterChip>
          <FilterChip selected={quick === "new"} onSelect={() => setQuick("new")} count={unreadNew}>Nouveau</FilterChip>
          <FilterChip selected={quick === "changed"} onSelect={() => setQuick("changed")} count={unreadChanged}>Modification</FilterChip>
        </div>
        <button
          type="button"
          className="btn btn-secondary filters-toggle"
          aria-expanded={facetsOpen}
          aria-controls="facets"
          onClick={() => setFacetsOpen((open) => !open)}
        >
          <SlidersHorizontal size={15} aria-hidden="true" /> Filtres
        </button>
        <div id="facets" className="facets" data-open={facetsOpen || undefined}>
          {!statusCounts && (
            <FacetSelect
              label="Statut de traitement"
              value={params.get("work_status") ?? ""}
              onChange={(value) => update({ work_status: value || null })}
              options={WORK_STATUSES.map((status) => ({ value: status, label: WORK_STATUS_LABELS[status] }))}
            />
          )}
          <FacetSelect
            label="Statut OmegaFlow"
            value={params.get("portal_status") ?? ""}
            onChange={(value) => update({ portal_status: value || null })}
            options={(page?.facets.portal_statuses ?? []).map((value) => ({ value, label: value }))}
          />
          <FacetSelect
            label="Procédure"
            value={params.get("procedure") ?? ""}
            onChange={(value) => update({ procedure: value || null })}
            options={(page?.facets.procedures ?? []).map((value) => ({ value, label: value }))}
            allLabel="Toutes"
          />
          {workflowKey === null && (
            <>
              <FacetSelect
                label="File"
                value={params.get("workflow") ?? ""}
                onChange={(value) => update({ workflow: value || null })}
                options={(workflows.data ?? []).filter((w) => w.enabled).map((w) => ({ value: w.key, label: w.name }))}
                allLabel="Toutes"
              />
              <FacetSelect
                label="Catégorie"
                value={params.get("category") ?? ""}
                onChange={(value) => update({ category: value || null })}
                options={categories.map((value) => ({ value, label: value }))}
                allLabel="Toutes"
              />
            </>
          )}
        </div>
        {activeFilters.length > 0 && (
          <Button variant="ghost" icon={<X size={14} aria-hidden="true" />} onClick={clearFilters}>
            Effacer les filtres
          </Button>
        )}
      </div>

      <p role="status" aria-live="polite" className="visually-hidden">
        {announcement}
      </p>
      {items.isError && page && <p className="banner banner-danger" role="alert">{items.error.message}</p>}

      <div
        id={panelId}
        role={statusCounts ? "tabpanel" : undefined}
        aria-labelledby={statusCounts ? `status-tab-${params.get("work_status") || "all"}` : undefined}
        className={previewed ? "list-layout list-layout-preview" : "list-layout"}
        aria-busy={items.isFetching || undefined}
      >
        <div className="card list-card">
          {body}
          {page && page.items.length > 0 && (
            <nav className="pagination" aria-label="Pagination">
              <span className="result-count" aria-live="polite">
                {from}–{to} sur {page.total} dossier{page.total > 1 ? "s" : ""}
                {sort.sort === "default" && <span className="muted hide-narrow"> · Non lus d’abord, puis date clé la plus récente</span>}
              </span>
              <span className="pagination-buttons">
                <Button icon={<ChevronLeft size={15} aria-hidden="true" />} disabled={current <= 1} onClick={() => update({ page: String(current - 1) }, false)}>
                  Précédent
                </Button>
                <span className="page-of">
                  Page {current} sur {totalPages}
                </span>
                <Button disabled={current >= totalPages} onClick={() => update({ page: String(current + 1) }, false)}>
                  Suivant <ChevronRight size={15} aria-hidden="true" />
                </Button>
              </span>
            </nav>
          )}
        </div>
        {previewed && canPreview && (
          <DossierPreviewPanel item={previewed} onClose={() => setPreviewId(null)} onOpen={(item) => void openItem(item)} />
        )}
      </div>
    </section>
  );
}
