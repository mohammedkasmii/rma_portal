import { ListFilter } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { useAdminWorkflows, useUpdateWorkflow } from "../../api/hooks";
import type { NotificationClass, RulesStatus, WorkflowView } from "../../api/types";
import { ClassBadge } from "../../components/Badges";
import { Toggle } from "../../components/ui/Form";
import { PageHeader } from "../../components/ui/PageHeader";
import { DelayedSkeleton, EmptyState, ErrorState } from "../../components/ui/States";
import { useToast } from "../../components/ui/Toast";
import { CLASS_LABELS, RULES_LABELS } from "../../labels";

const CLASSES: NotificationClass[] = ["ACTION", "INFORMATIONAL", "SILENT"];
const RULES: RulesStatus[] = ["UNCONFIRMED", "CAPTURE_DERIVED", "CONFIRMED"];

export function AdminWorkflows() {
  const workflows = useAdminWorkflows();
  const update = useUpdateWorkflow();
  const toast = useToast();
  const [filter, setFilter] = useState("");

  const groups = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase("fr");
    const map = new Map<string, WorkflowView[]>();
    for (const w of workflows.data ?? []) {
      if (needle && !`${w.name} ${w.category}`.toLocaleLowerCase("fr").includes(needle)) continue;
      map.set(w.category, [...(map.get(w.category) ?? []), w]);
    }
    return [...map.entries()];
  }, [workflows.data, filter]);

  if (workflows.isLoading) {
    return (
      <>
        <PageHeader title="Configuration des files" />
        <DelayedSkeleton rows={8} label="Chargement de la configuration…" />
      </>
    );
  }
  if (!workflows.data) {
    return (
      <>
        <PageHeader title="Configuration des files" />
        <ErrorState message={workflows.error?.message} onRetry={() => void workflows.refetch()} />
      </>
    );
  }

  const save = (change: Parameters<typeof update.mutate>[0]) =>
    update.mutate(change, {
      onSuccess: () => toast({ message: "Configuration enregistrée." }),
      onError: (error) => toast({ tone: "error", message: error.message }),
    });
  const toValidate = workflows.data.filter((w) => w.enabled && w.rules_status !== "CONFIRMED").length;

  return (
    <>
      <PageHeader
        title="Configuration des files"
        description={
          <>
            {groups.length} catégorie{groups.length > 1 ? "s" : ""} · {workflows.data.length} files
            {toValidate > 0 && ` · ${toValidate} à valider sur site`}. Passer une règle à « Confirmé » ne change pas le moteur d’événements ;
            une file désactivée n’est pas lue.
          </>
        }
      />

      <div className="filter-bar">
        <div className="filter-input">
          <ListFilter size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Filtrer les files"
            placeholder="Filtrer les files…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>
      </div>
      <p role="status" aria-live="polite" className="error-text">{update.isError && update.error.message}</p>

      <div className="card list-card">
        {groups.length === 0 ? (
          <EmptyState title="Aucune file ne correspond" />
        ) : (
          <div className="table-wrap" role="region" aria-label="Configuration" tabIndex={0}>
            <table className="admin-table">
              <caption className="visually-hidden">Activation, règle et type de notification par file</caption>
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Activée</th>
                  <th scope="col">Règle</th>
                  <th scope="col">Notification</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(([category, list]) => (
                  <Fragment key={category}>
                    <tr className="group-row">
                      <th scope="colgroup" colSpan={4}>{category}</th>
                    </tr>
                    {list.map((w) => (
                      <tr key={w.key}>
                        <th scope="row">{w.name}</th>
                        <td>
                          <Toggle label={`Activer ${w.name}`} checked={w.enabled} onChange={(enabled) => save({ key: w.key, enabled })} />
                        </td>
                        <td>
                          <label className="visually-hidden" htmlFor={`rules-${w.key}`}>Règle de {w.name}</label>
                          <select
                            id={`rules-${w.key}`}
                            className="input input-sm"
                            value={w.rules_status}
                            onChange={(event) => save({ key: w.key, rules_status: event.target.value as RulesStatus })}
                          >
                            {RULES.map((r) => (
                              <option key={r} value={r}>{RULES_LABELS[r]}</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <span className="inline-controls">
                            <label className="visually-hidden" htmlFor={`class-${w.key}`}>Notification de {w.name}</label>
                            <select
                              id={`class-${w.key}`}
                              className="input input-sm"
                              value={w.notification_class}
                              onChange={(event) => save({ key: w.key, notification_class: event.target.value as NotificationClass })}
                            >
                              {CLASSES.map((c) => (
                                <option key={c} value={c}>{CLASS_LABELS[c]}</option>
                              ))}
                            </select>
                            <ClassBadge value={w.notification_class} />
                          </span>
                        </td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
