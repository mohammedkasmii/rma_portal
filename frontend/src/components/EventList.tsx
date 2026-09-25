import { Link } from "react-router-dom";
import type { EventOut } from "../api/types";
import { EVENT_LABELS, formatDateTime } from "../labels";
import { ClassBadge } from "./Badges";

interface Props {
  events: EventOut[];
  showDossier?: boolean;
  empty?: string;
}

/** Activity feed / timeline. Class is shown as icon + word, and informational events are quieter. */
export function EventList({ events, showDossier = true, empty = "Aucune activité récente." }: Props) {
  if (events.length === 0) return <p className="empty">{empty}</p>;
  return (
    <ol className="events">
      {events.map((event) => (
        <li key={event.id} className={`event event-${event.notification_class.toLowerCase()}`}>
          <div className="event-head">
            <ClassBadge value={event.notification_class} />
            <strong>{EVENT_LABELS[event.kind] ?? event.kind}</strong>
            <time dateTime={event.detected_at}>{formatDateTime(event.detected_at)}</time>
          </div>
          <div className="event-body">
            {event.workflow_name && <span>{event.workflow_name}</span>}
            {showDossier && event.dossier_id && (
              <span>
                {" · "}
                <Link to={`/dossiers/${event.dossier_id}`}>
                  {event.dossier_number || `Dossier ${event.dossier_id}`}
                </Link>
                {event.insured_name ? ` — ${event.insured_name}` : ""}
              </span>
            )}
            {event.changed_labels.length > 0 && <span> · Champs modifiés : {event.changed_labels.join(", ")}</span>}
            {event.message && <span> · {event.message}</span>}
          </div>
        </li>
      ))}
    </ol>
  );
}
