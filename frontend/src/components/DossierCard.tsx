import type { ItemView } from "../api/types";
import { formatWhen } from "../labels";
import { ChangeMarker, OmegaFlowStatusTag } from "./ui/Badges";
import { TreatmentStatusControl } from "./ui/TreatmentStatusControl";

interface Props {
  items: ItemView[];
  caption: string;
  showWorkflow: boolean;
  onOpen: (item: ItemView) => void;
}

/** Mobile list: one card per dossier — number, marker, time, insured, plate, file and both statuses. */
export function DossierCards({ items, caption, showWorkflow, onOpen }: Props) {
  return (
    <ul className="cards" aria-label={caption}>
      {items.map((item) => {
        const number = item.dossier_number || item.record_id;
        return (
          <li
            key={item.membership_id}
            className={item.unread ? "dossier-card dossier-card-unread" : "dossier-card"}
          >
            <div className="card-line">
              <button type="button" className="row-link row-link-stretched mono" onClick={() => onOpen(item)}>
                {number}
                <span className="visually-hidden"> — ouvrir le dossier de {item.insured_name}</span>
              </button>
              <ChangeMarker kind={item.unread_kind} />
              <span className="card-when">{formatWhen(item.detected_at)}</span>
            </div>
            <p className="card-title">
              {item.insured_name}
              {item.registration && <span className="mono muted"> · {item.registration}</span>}
            </p>
            {showWorkflow && <p className="card-file">{item.workflow_name}</p>}
            <div className="card-foot">
              <OmegaFlowStatusTag value={item.portal_status} />
              <TreatmentStatusControl
                membershipId={item.membership_id}
                status={item.work_status}
                version={item.work_version}
                label={`Statut de traitement de ${number}`}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
