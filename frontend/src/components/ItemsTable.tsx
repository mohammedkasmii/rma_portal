import { createColumnHelper, flexRender, type ColumnDef, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useMemo } from "react";
import type { ColumnView, ItemView } from "../api/types";
import { formatAge, formatDateTime } from "../labels";
import { ClassBadge, RulesBadge, UnreadBadge } from "./Badges";
import { WorkStatusControl } from "./WorkStatusControl";

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
}

const helper = createColumnHelper<ItemView>();

/** Server-driven table (manual sorting/pagination): the API returns the page already ordered. */
export function ItemsTable({ caption, items, extraColumns = [], showWorkflow = true, sort, onSort, onOpen }: Props) {
  const columns = useMemo(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cols: ColumnDef<ItemView, any>[] = [
      helper.display({
        id: "unread",
        header: "Alerte",
        cell: ({ row }) => <UnreadBadge kind={row.original.unread_kind} />,
      }),
      helper.accessor("dossier_number", {
        id: "number",
        header: "Dossier",
        cell: ({ row }) => (
          <button type="button" className="link-button" onClick={() => onOpen(row.original)}>
            {row.original.dossier_number || row.original.record_id}
            <span className="visually-hidden"> — ouvrir le dossier de {row.original.insured_name}</span>
          </button>
        ),
      }),
      helper.accessor("insured_name", { id: "name", header: "Assuré" }),
      helper.accessor("registration", { header: "Immat.", enableSorting: false }),
      helper.accessor("garage", { header: "Garage", enableSorting: false }),
      helper.accessor("portal_status", { header: "Statut OmegaFlow", enableSorting: false }),
    ];
    if (showWorkflow) {
      cols.push(
        helper.display({
          id: "workflow",
          header: "Workflow",
          cell: ({ row }) => (
            <span className="stack">
              {row.original.workflow_name}
              <span className="badges">
                <ClassBadge value={row.original.notification_class} />
                <RulesBadge value={row.original.rules_status} />
              </span>
            </span>
          ),
        }),
      );
    }
    for (const column of extraColumns) {
      cols.push(
        helper.display({
          id: `f_${column.key}`,
          header: column.label,
          cell: ({ row }) => row.original.fields[column.key] || "—",
        }),
      );
    }
    cols.push(
      helper.display({
        id: "date",
        header: "Date clé",
        cell: ({ row }) => (
          <span className="stack">
            {row.original.primary_date_raw || formatDateTime(row.original.detected_at)}
            <small>{row.original.primary_date_label ?? "détection"}</small>
          </span>
        ),
      }),
      helper.display({
        id: "age",
        header: "Dans la file",
        cell: ({ row }) => formatAge(row.original.queue_age_days),
      }),
      helper.display({
        id: "status",
        header: "Traitement",
        cell: ({ row }) => (
          <WorkStatusControl
            membershipId={row.original.membership_id}
            status={row.original.work_status}
            version={row.original.work_version}
            label={`Statut de ${row.original.dossier_number || row.original.record_id}`}
          />
        ),
      }),
    );
    return cols;
  }, [extraColumns, onOpen, showWorkflow]);

  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: items,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    manualFiltering: true,
    getRowId: (row) => String(row.membership_id),
  });

  const sortable = new Set(["number", "name", "date", "age", "status", "workflow"]);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const id = header.column.id;
                const active = sort.sort === id;
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={active ? (sort.descending ? "descending" : "ascending") : undefined}
                  >
                    {sortable.has(id) ? (
                      <button type="button" className="sort-button" onClick={() => onSort(id)}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden="true">{active ? (sort.descending ? " ▼" : " ▲") : " ↕"}</span>
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className={row.original.unread ? "row-unread" : undefined}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} data-label={String(cell.column.columnDef.header)}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
          {items.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="empty">
                Aucun dossier ne correspond à ces critères.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
