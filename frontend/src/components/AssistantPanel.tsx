import { useState } from "react";
import { useAiRun, useAiStatus } from "../api/hooks";
import type { AiOutcome, MembershipView } from "../api/types";

type Scope =
  | { kind: "dashboard" }
  | { kind: "dossier"; dossierId: number; memberships: MembershipView[] };

interface Entry {
  label: string;
  outcome: AiOutcome;
}

const FALLBACK = "L’assistant local est indisponible pour le moment.";

function ResultBody({ outcome }: { outcome: AiOutcome }) {
  if (outcome.status !== "OK" && outcome.status !== "NO_DATA") {
    return <p role="status">{outcome.message ?? FALLBACK}</p>;
  }
  const result = (outcome.result ?? {}) as Record<string, unknown>;
  const lists = ["highlights", "attention", "key_points", "open_questions"] as const;
  return (
    <div>
      {typeof result.headline === "string" && <p><strong>{result.headline}</strong></p>}
      {typeof result.summary === "string" && <p>{result.summary}</p>}
      {typeof result.explanation === "string" && <p>{result.explanation}</p>}
      {typeof result.priority === "string" && (
        <p>
          Priorité suggérée : <strong>{result.priority}</strong> — {String(result.next_action ?? "")}
          <br />
          <small>{String(result.rationale ?? "")}</small>
        </p>
      )}
      {lists.map((name) => {
        const values = result[name];
        return Array.isArray(values) && values.length > 0 ? (
          <ul key={name}>
            {values.map((v) => (
              <li key={String(v)}>{String(v)}</li>
            ))}
          </ul>
        ) : null;
      })}
      {Array.isArray(result.groups) &&
        (result.groups as Array<{ title: string; summary: string }>).map((g) => (
          <p key={g.title}>
            <strong>{g.title}</strong> — {g.summary}
          </p>
        ))}
      {outcome.status === "NO_DATA" && <p>{outcome.message}</p>}
      {outcome.facts.length > 0 && (
        <details>
          <summary>Faits utilisés</summary>
          <ul>
            {outcome.facts.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </details>
      )}
      <p className="muted"><small>{outcome.disclaimer}{outcome.cached ? " (résultat déjà calculé)" : ""}</small></p>
    </div>
  );
}

/**
 * Optional local assistant. It renders nothing unless the server reports it enabled, and every
 * failure is shown as a neutral message: the rest of the page never depends on it.
 */
export function AssistantPanel({ scope }: { scope: Scope }) {
  const status = useAiStatus();
  const run = useAiRun();
  const [entries, setEntries] = useState<Entry[]>([]);

  if (!status.data?.enabled) return null;

  const launch = (label: string, path: string) =>
    run.mutate(path, {
      onSuccess: (outcome) => setEntries((current) => [{ label, outcome }, ...current].slice(0, 6)),
    });
  const current = scope.kind === "dossier" ? scope.memberships[0] : undefined;

  return (
    <details className="card assistant" aria-label="Assistant local">
      <summary>Assistant local — suggestions{status.data.healthy === false ? " (hors ligne)" : ""}</summary>
      <p className="muted">
        <small>Modèle {status.data.model}. Suggestions fondées sur les données synchronisées ; aucune action n’est effectuée.</small>
      </p>
      <div className="actions">
        {scope.kind === "dashboard" ? (
          <>
            <button type="button" disabled={run.isPending} onClick={() => launch("Résumé de la journée", "/ai/daily-summary")}>
              Résumé de la journée
            </button>
            <button type="button" disabled={run.isPending} onClick={() => launch("Anomalies", "/ai/anomalies")}>
              Grouper les anomalies
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={run.isPending}
              onClick={() => launch("Résumé du dossier", `/dossiers/${scope.dossierId}/ai/summary`)}
            >
              Résumer ce dossier
            </button>
            {current && (
              <>
                <button
                  type="button"
                  disabled={run.isPending}
                  onClick={() => launch(`Pourquoi (${current.workflow_name})`, `/occurrences/${current.current_occurrence_id}/ai/explain`)}
                >
                  Pourquoi est-il mis en avant ?
                </button>
                <button
                  type="button"
                  disabled={run.isPending}
                  onClick={() => launch(`Priorité (${current.workflow_name})`, `/memberships/${current.membership_id}/ai/priority`)}
                >
                  Suggérer une priorité
                </button>
              </>
            )}
          </>
        )}
      </div>
      <div aria-live="polite">
        {run.isPending && <p>Analyse en cours…</p>}
        {run.isError && <p role="status">{FALLBACK}</p>}
        {entries.map((entry, index) => (
          <section key={`${entry.label}-${index}`} aria-label={entry.label}>
            <h3>{entry.label}</h3>
            <ResultBody outcome={entry.outcome} />
          </section>
        ))}
      </div>
    </details>
  );
}
