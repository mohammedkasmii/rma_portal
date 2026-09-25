import { PlugZap, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { buttonClass } from "./Button";

interface Props {
  audience: "employee" | "admin";
  /** Formatted last refresh time, when known. */
  lastRefresh?: string | null;
}

/**
 * Session-expired notice. Employees get a neutral warning without technical detail or action;
 * administrators get a persistent alert with the reconnect action.
 */
export function SyncBanner({ audience, lastRefresh }: Props) {
  if (audience === "admin") {
    return (
      <div role="alert" className="banner banner-danger">
        <PlugZap size={18} aria-hidden="true" />
        <p>
          <strong>Session OmegaFlow expirée.</strong> Les files ne sont plus actualisées
          {lastRefresh ? ` depuis ${lastRefresh}` : ""}. Les collaborateurs voient un avertissement.
        </p>
        <Link to="/admin/session" className={`${buttonClass("primary")} btn-on-danger`}>
          Reconnecter
        </Link>
      </div>
    );
  }
  return (
    <div role="status" className="banner banner-warn">
      <TriangleAlert size={18} aria-hidden="true" />
      <p>
        <strong>Les données ne sont pas actualisées actuellement.</strong>
        {lastRefresh ? ` Dernière actualisation : ${lastRefresh}.` : ""}
      </p>
    </div>
  );
}
