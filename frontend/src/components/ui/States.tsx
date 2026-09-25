import { CircleAlert, Inbox } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "./Button";

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="state">
      <span className="state-icon" aria-hidden="true">
        {icon ?? <Inbox size={22} />}
      </span>
      <p className="state-title">{title}</p>
      {children && <p className="state-body">{children}</p>}
    </div>
  );
}

interface ErrorProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export function ErrorState({ title = "Impossible de charger les données", message, onRetry }: ErrorProps) {
  return (
    <div className="state state-error" role="alert">
      <span className="state-icon" aria-hidden="true">
        <CircleAlert size={22} />
      </span>
      <p className="state-title">{title}</p>
      <p className="state-body">
        {message ?? "Le portail ne répond pas. Vos modifications déjà enregistrées ne sont pas perdues."}
      </p>
      {onRetry && <Button onClick={onRetry}>Réessayer</Button>}
    </div>
  );
}

/** True once the component has been mounted for `delay` ms: short loads never flash a skeleton. */
function useDelayedMount(delay = 300): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setReady(true), delay);
    return () => window.clearTimeout(timer);
  }, [delay]);
  return ready;
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <span className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

/** Placeholder rows shown while a list loads; announces the loading state to assistive tech. */
export function SkeletonRows({ rows = 6, label = "Chargement…" }: { rows?: number; label?: string }) {
  return (
    <div className="skeleton-rows" role="status">
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} height={40} />
      ))}
    </div>
  );
}

/** Loading placeholder that stays invisible for the first 300 ms, then shows skeleton rows. */
export function DelayedSkeleton({ rows, label }: { rows?: number; label?: string }) {
  const show = useDelayedMount();
  return show ? <SkeletonRows rows={rows} label={label} /> : <span className="visually-hidden" role="status">{label ?? "Chargement…"}</span>;
}
