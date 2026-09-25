import { Menu } from "lucide-react";
import type { SyncStatus } from "../../syncStatus";
import { GlobalSearch } from "./GlobalSearch";

interface Props {
  sync: SyncStatus;
  showMenuButton: boolean;
  menuOpen: boolean;
  onMenu: () => void;
}

const DOT_LABELS = { ok: "Données à jour", stale: "Données partiellement à jour", expired: "Données non actualisées" } as const;

export function Topbar({ sync, showMenuButton, menuOpen, onMenu }: Props) {
  return (
    <header className="topbar">
      {showMenuButton && (
        <button
          type="button"
          className="icon-btn"
          aria-label="Ouvrir le menu"
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          onClick={onMenu}
        >
          <Menu size={22} aria-hidden="true" />
        </button>
      )}
      <GlobalSearch />
      <div className="topbar-spacer" />
      <div className="sync-status" role="status">
        <span className={`sync-dot sync-dot-${sync.level}`} aria-hidden="true" />
        <span className="visually-hidden">{DOT_LABELS[sync.level]} — </span>
        <span className="sync-label">{sync.label}</span>
      </div>
    </header>
  );
}
