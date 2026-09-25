import { useRef, type KeyboardEvent } from "react";
import type { WorkStatus } from "../api/types";
import { WORK_STATUSES, WORK_STATUS_LABELS } from "../labels";

interface Props {
  /** Selected treatment status, or "" for every dossier of the queue. */
  value: string;
  onChange: (value: string) => void;
  /** Counts returned by the API for the queue (active dossiers per treatment status). */
  counts: Record<string, number>;
  total: number;
  panelId: string;
}

/** Queue tabs by Statut de traitement, with the counts the queue endpoint already provides. */
export function StatusTabs({ value, onChange, counts, total, panelId }: Props) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const tabs: Array<{ key: string; label: string; count: number }> = [
    { key: "", label: "Tous", count: total },
    ...WORK_STATUSES.map((status: WorkStatus) => ({ key: status, label: WORK_STATUS_LABELS[status], count: counts[status] ?? 0 })),
  ];
  const current = Math.max(0, tabs.findIndex((tab) => tab.key === value));

  const onKey = (event: KeyboardEvent, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    onChange(tabs[next].key);
    refs.current[next]?.focus();
  };

  return (
    <div role="tablist" aria-label="Statut de traitement" className="status-tabs">
      {tabs.map((tab, index) => (
        <button
          key={tab.key || "all"}
          ref={(node) => {
            refs.current[index] = node;
          }}
          type="button"
          role="tab"
          id={`status-tab-${tab.key || "all"}`}
          aria-selected={index === current}
          aria-controls={panelId}
          tabIndex={index === current ? 0 : -1}
          className="status-tab"
          onClick={() => onChange(tab.key)}
          onKeyDown={(event) => onKey(event, index)}
        >
          {tab.label}
          <span className="count count-neutral">{tab.count}</span>
        </button>
      ))}
    </div>
  );
}
