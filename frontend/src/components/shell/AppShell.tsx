import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useDashboard } from "../../api/hooks";
import { useAuth } from "../../auth";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { dashboardSyncStatus } from "../../syncStatus";
import { SyncBanner } from "../ui/SyncBanner";
import { Sidebar, type SidebarMode } from "./Sidebar";
import { Topbar } from "./Topbar";

type RailPreference = "rail" | "expanded" | null;
const RAIL_KEY = "rma-sidebar";

function readRailPreference(): RailPreference {
  try {
    const stored = localStorage.getItem(RAIL_KEY);
    return stored === "rail" || stored === "expanded" ? stored : null;
  } catch {
    return null;
  }
}

/**
 * Shell: sidebar (full ≥ 1280 px, rail 1024–1279 px unless the user chose otherwise, drawer below),
 * top bar with global search and freshness, session banner and the routed page.
 */
export function AppShell() {
  const { isAdmin } = useAuth();
  const location = useLocation();
  const dashboard = useDashboard();
  const wide = useMediaQuery("(min-width: 1280px)");
  const desktop = useMediaQuery("(min-width: 1024px)");
  const [railPref, setRailPref] = useState<RailPreference>(readRailPreference);
  // The drawer is tied to the page it was opened on, so navigating closes it without an effect.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const drawerOpen = openOn === location.pathname;

  const mode: SidebarMode = !desktop ? "drawer" : railPref === "rail" || (railPref === null && !wide) ? "rail" : "full";
  const sync = dashboardSyncStatus(dashboard.data);

  const chooseRail = (next: Exclude<RailPreference, null>) => {
    setRailPref(next);
    try {
      localStorage.setItem(RAIL_KEY, next);
    } catch {
      /* the choice still applies for this visit */
    }
  };

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenOn(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  return (
    <div className={`shell shell-${mode}`} data-drawer={mode === "drawer" ? (drawerOpen ? "open" : "closed") : undefined}>
      <a className="skip-link" href="#main">
        Aller au contenu
      </a>
      <Sidebar
        mode={mode}
        onNavigate={() => setOpenOn(null)}
        onToggleRail={() => chooseRail(mode === "rail" ? "expanded" : "rail")}
        onExpandFromRail={() => chooseRail("expanded")}
        onCloseDrawer={() => setOpenOn(null)}
      />
      {mode === "drawer" && drawerOpen && <div className="scrim" onClick={() => setOpenOn(null)} aria-hidden="true" />}
      <div className="shell-body">
        <Topbar
          sync={sync}
          showMenuButton={mode === "drawer"}
          menuOpen={drawerOpen}
          onMenu={() => setOpenOn(drawerOpen ? null : location.pathname)}
        />
        {sync.level === "expired" && <SyncBanner audience={isAdmin ? "admin" : "employee"} lastRefresh={sync.lastRefresh} />}
        <main id="main" tabIndex={-1} inert={drawerOpen || undefined}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
