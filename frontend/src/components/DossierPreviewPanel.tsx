import { ExternalLink, X } from "lucide-react";
import type { ItemView } from "../api/types";
import { formatAge, formatDateTime } from "../labels";
import { ChangeMarker, OmegaFlowStatusTag } from "./ui/Badges";
import { buttonClass, Button } from "./ui/Button";
import { TreatmentStatusControl } from "./ui/TreatmentStatusControl";

interface Props {
  item: ItemView;
  onClose: () => void;
  onOpen: (item: ItemView) => void;
}

/** Side preview of a queue row (≥ 1280 px). Only shows what the list API already returned. */
export function DossierPreviewPanel({ item, onClose, onOpen }: Props) {
  const number = item.dossier_number || item.record_id;
  const facts: Array<[string, string]> = [
    ["Garage", item.garage],
    ["Procédure", item.procedure],
    ["Ville", item.city],
    [item.primary_date_label ?? "Date clé", item.primary_date_raw],
  ].filter((entry): entry is [string, string] => !!entry[1]);
  return (
    <aside className="preview card" aria-label="Aperçu du dossier">
      <div className="preview-head">
        <div className="preview-title">
          <span className="mono preview-number">{number}</span>
          <ChangeMarker kind={item.unread_kind} />
          <span className="spacer" />
          <button type="button" className="icon-btn" aria-label="Fermer l’aperçu" onClick={onClose}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <p className="preview-sub">
          {item.insured_name}
          {item.registration && <span className="mono"> · {item.registration}</span>}
        </p>
      </div>
      <dl className="preview-grid">
        <div>
          <dt>Statut OmegaFlow</dt>
          <dd><OmegaFlowStatusTag value={item.portal_status} /></dd>
        </div>
        <div>
          <dt>Dernier changement</dt>
          <dd>{formatDateTime(item.detected_at)}</dd>
        </div>
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
        <div>
          <dt>Dans la file</dt>
          <dd>{formatAge(item.queue_age_days)}</dd>
        </div>
      </dl>
      <div className="preview-status">
        <p className="preview-label">
          Statut de traitement <span className="muted">· partagé</span>
        </p>
        <TreatmentStatusControl
          key={`${item.membership_id}-${item.work_version}`}
          membershipId={item.membership_id}
          status={item.work_status}
          version={item.work_version}
          label={`Statut de traitement de ${number} (aperçu)`}
        />
      </div>
      <div className="preview-actions">
        <Button onClick={() => onOpen(item)}>Ouvrir la fiche</Button>
        <a className={buttonClass("primary")} href={item.omegaflow_url} target="_blank" rel="noopener noreferrer">
          Ouvrir dans OmegaFlow
          <ExternalLink size={14} aria-hidden="true" />
          <span className="visually-hidden"> (nouvel onglet)</span>
        </a>
      </div>
    </aside>
  );
}
