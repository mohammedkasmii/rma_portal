import { ItemsBrowser } from "../components/ItemsBrowser";
import { PageHeader } from "../components/ui/PageHeader";

export function Inbox() {
  return (
    <ItemsBrowser
      workflowKey={null}
      caption="Dossiers actifs de toutes les files"
      filterLabel="Filtrer cette liste…"
      emptyTitle="Rien à traiter pour le moment"
      header={(total) => (
        <PageHeader
          title="À traiter"
          count={total}
          description="Dossiers actifs de toutes les files, alertes non lues en premier."
        />
      )}
    />
  );
}
