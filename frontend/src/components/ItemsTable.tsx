import { ArrowDown, ArrowUp, ArrowUpDown, Eye, ExternalLink } from "lucide-react";
import type { MouseEvent } from "react";
import type { ColumnView, ItemView } from "../api/types";
import { formatAge, formatWhen } from "../labels";
import { ChangeMarker, OmegaFlowStatusTag } from "./ui/Badges";
import { TreatmentStatusControl } from "./ui/TreatmentStatusControl";

export interface SortState {
  sort: string;
  descending: boolean;
}

interface Props {
  caption: string;
  items: ItemView[];
  extraColumns?: ColumnView[];
  showWorkflow?: boolean;
  sort: SortState;
  onSort: (key: string) => void;
  onOpen: (item: ItemView) => void;
  /** Present on queue pages wide enough for the side preview. */
  onPreview?: (item: ItemView) => void;
  previewedId?: number | null;
}

/** Rows react to a click anywhere except on their own controls (status menu, links, buttons). */
export function isRowClick(event: MouseEvent<HTMLElement>): boolean {
  return !(event.target as HTMLElement).closest("button, a, select, input, label");
}

export function dateLine(item: ItemView): string {
  if (item.primary_date_raw) return `${item.primary_date_label ?? "Date clé"} : ${item.primary_date_raw}`;
  return formatAge(item.queue_age_days);
}

function SortHeader({ id, label, sort, onSort }: { id: string; label: string; sort: SortState; onSort: (key: string) => void }) {
  const active = sort.sort === id;
  const Icon = !active ? ArrowUpDown : sort.descending ? ArrowDown : ArrowUp;
  return (
    <button type="button" className="sort-btn" data-active={active || undefined} onClick={() => onSort(id)}>
      {label}
      <Icon size={13} aria-hidden="true" />
    </button>
  );
}

/** Server-driven table (manual sorting/pagination): the API returns the page already ordered. */
export function ItemsTable({
  caption,
  items,
  extraColumns = [],
  showWorkflow = true,
  sort,
  onSort,
  onOpen,
  onPreview,
  previewedId,
}: Props) {
  const ariaSort = (id: string) => (sort.sort === id ? (sort.descending ? "descending" : "ascending") : undefined);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className="items">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="col-dot">
              <span className="visually-hidden">Alerte</span>
            </th>
            <th scope="col" aria-sort={ariaSort("number") ?? ariaSort("name")} className="col-main">
              <SortHeader id="number" label="Dossier" sort={sort} onSort={onSort} />
              <span aria-hidden="true"> · </span>
              <SortHeader id="name" label="Assuré" sort={sort} onSort={onSort} />
            </th>
            <th scope="col" className="col-immat">Immat.</th>
            {showWorkflow && (
              <th scope="col" aria-sort={ariaSort("workflow")} className="col-file">
                <SortHeader id="workflow" label="File" sort={sort} onSort={onSort} />
              </th>
            )}
            <th scope="col" className="col-omega">Statut OmegaFlow</th>
            <th scope="col" aria-sort={ariaSort("status")} className="col-treat">
              <SortHeader id="status" label="Statut de traitement" sort={sort} onSort={onSort} />
            </th>
            {extraColumns.map((column) => (
              <th key={column.key} scope="col" className="col-extra">{column.label}</th>
            ))}
            <th scope="col" aria-sort={ariaSort("date")} className="col-date">
              <SortHeader id="date" label="Date clé" sort={sort} onSort={onSort} />
            </th>
            <th scope="col" aria-sort={ariaSort("detected")} className="col-change">
              <SortHeader id="detected" label="Dernier changement" sort={sort} onSort={onSort} />
            </th>
            <th scope="col" className="col-actions">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const number = item.dossier_number || item.record_id;
            return (
              <tr
                key={item.membership_id}
                className={[item.unread ? "row-unread" : "", previewedId === item.membership_id ? "row-selected" : ""].filter(Boolean).join(" ")}
                onClick={(event) => {
                  if (isRowClick(event)) onOpen(item);
                }}
              >
                <td className="col-dot">{item.unread && <span className="unread-dot" title="Non lu" aria-hidden="true" />}</td>
                <td className="col-main">
                  <div className="stack">
                    <button type="button" className="row-link mono" onClick={() => onOpen(item)}>
                      {number}
                      <span className="visually-hidden"> — ouvrir le dossier de {item.insured_name}</span>
                    </button>
                    <span className="row-sub">{item.insured_name}</span>
                  </div>
                </td>
                <td className="col-immat mono">{item.registration || "—"}</td>
                {showWorkflow && (
                  <td className="col-file">
                    <span className="clamp" title={item.workflow_name}>{item.workflow_name}</span>
                  </td>
                )}
                <td className="col-omega"><OmegaFlowStatusTag value={item.portal_status} /></td>
                <td className="col-treat">
                  <TreatmentStatusControl
                    membershipId={item.membership_id}
                    status={item.work_status}
                    version={item.work_version}
                    label={`Statut de traitement de ${number}`}
                  />
                </td>
                {extraColumns.map((column) => (
                  <td key={column.key} className="col-extra">{item.fields[column.key] || "—"}</td>
                ))}
                <td className="col-date"><span className="clamp">{dateLine(item)}</span></td>
                <td className="col-change">
                  <div className="stack">
                    <span className="change-line">
                      <ChangeMarker kind={item.unread_kind} />
                      <span className="tabular">{formatWhen(item.detected_at)}</span>
                    </span>
                    <span className="row-sub col-date-inline">{dateLine(item)}</span>
                  </div>
                </td>
                <td className="col-actions">
                  <span className="row-actions">
                    {onPreview && (
                      <button
                        type="button"
                        className="icon-btn"
                        aria-label={`Aperçu du dossier ${number}`}
                        aria-pressed={previewedId === item.membership_id}
                        onClick={() => onPreview(item)}
                      >
                        <Eye size={16} aria-hidden="true" />
                      </button>
                    )}
                    <a
                      className="icon-btn"
                      href={item.omegaflow_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Ouvrir ${number} dans OmegaFlow (nouvel onglet)`}
                      title="Ouvrir dans OmegaFlow"
                    >
                      <ExternalLink size={15} aria-hidden="true" />
                    </a>
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
