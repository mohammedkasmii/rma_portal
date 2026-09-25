import {
  Activity,
  ChevronDown,
  ChevronRight,
  Folder,
  House,
  Inbox,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PlugZap,
  SlidersHorizontal,
  Sun,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useDashboard, useWorkflows } from "../../api/hooks";
import type { WorkflowView } from "../../api/types";
import { useAuth } from "../../auth";
import { dashboardSyncStatus } from "../../syncStatus";
import { useTheme, type ThemePreference } from "../../theme";
import { CountBadge } from "../ui/Badges";
import { ThemeSelector } from "../ui/ThemeSelector";

export type SidebarMode = "full" | "rail" | "drawer";

interface Props {
  mode: SidebarMode;
  /** Called after any navigation, so the mobile drawer can close. */
  onNavigate: () => void;
  onToggleRail: () => void;
  onExpandFromRail: () => void;
  onCloseDrawer: () => void;
}

const unreadOf = (workflow: WorkflowView) => workflow.unread_new + workflow.unread_changed;
const ROLE_LABELS: Record<string, string> = { ADMIN: "Administrateur", EMPLOYEE: "Collaborateur" };
const THEME_CYCLE: Record<ThemePreference, { next: ThemePreference; icon: LucideIcon; label: string }> = {
  light: { next: "dark", icon: Sun, label: "Clair" },
  dark: { next: "system", icon: Moon, label: "Sombre" },
  system: { next: "light", icon: Monitor, label: "Système" },
};

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/** Categories the user opened by hand, remembered per user on this workstation. */
function useOpenCategories(userId: number | undefined) {
  const storageKey = `rma-nav-open-${userId ?? "anon"}`;
  const [open, setOpen] = useState<string[]>(() => {
    try {
      const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) ?? "[]");
      return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
    } catch {
      return [];
    }
  });
  const save = (next: string[]) => {
    setOpen(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* remembering the state is a convenience only */
    }
  };
  return [open, save] as const;
}

export function Sidebar({ mode, onNavigate, onToggleRail, onExpandFromRail, onCloseDrawer }: Props) {
  const { user, isAdmin, logout } = useAuth();
  const workflows = useWorkflows();
  const dashboard = useDashboard();
  const location = useLocation();
  const { preference, setPreference } = useTheme();
  const [openCategories, saveOpen] = useOpenCategories(user?.id);
  const rail = mode === "rail";

  const groups = useMemo(() => {
    const map = new Map<string, WorkflowView[]>();
    for (const workflow of workflows.data ?? []) {
      if (!workflow.enabled && !isAdmin) continue; // disabled queues are only visible to administrators
      map.set(workflow.category, [...(map.get(workflow.category) ?? []), workflow]);
    }
    return [...map.entries()];
  }, [workflows.data, isAdmin]);

  const activeKey = /^\/workflows\/([^/]+)/.exec(location.pathname)?.[1];
  const activeCategory = groups.find(([, list]) => list.some((workflow) => workflow.key === activeKey))?.[0];
  const isOpen = (category: string) => category === activeCategory || openCategories.includes(category);
  const totalUnread = (workflows.data ?? []).filter((w) => w.enabled).reduce((sum, w) => sum + unreadOf(w), 0);
  const sync = dashboardSyncStatus(dashboard.data);

  const toggle = (category: string, force?: boolean) => {
    const wanted = force ?? !isOpen(category);
    saveOpen(wanted ? [...new Set([...openCategories, category])] : openCategories.filter((item) => item !== category));
  };
  const onCategoryKey = (event: KeyboardEvent, category: string) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      toggle(category, true);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      toggle(category, false);
    }
  };

  const themeStep = THEME_CYCLE[preference];
  const ThemeIcon = themeStep.icon;
  const name = user?.display_name ?? "";

  const navItem = (to: string, label: string, Icon: LucideIcon, extra?: ReactNode, end = false) => (
    <NavLink to={to} end={end} className="nav-item" onClick={onNavigate} title={rail ? label : undefined}>
      <Icon size={16} aria-hidden="true" />
      <span className="nav-label">{label}</span>
      {extra}
    </NavLink>
  );

  return (
    <nav id="sidebar" className={`sidebar sidebar-${mode}`} aria-label="Navigation principale">
      <div className="sidebar-head">
        <Link to="/" className="brand" onClick={onNavigate} aria-label="Portail RMA — Accueil">
          <span className="brand-mark" aria-hidden="true">PR</span>
          <span className="brand-name">Portail RMA</span>
        </Link>
        {mode === "drawer" ? (
          <button type="button" className="icon-btn" aria-label="Fermer le menu" onClick={onCloseDrawer}>
            <X size={20} aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            className="icon-btn sidebar-toggle"
            aria-label={rail ? "Développer la navigation" : "Réduire la navigation"}
            aria-expanded={!rail}
            aria-controls="sidebar"
            onClick={onToggleRail}
          >
            {rail ? <PanelLeftOpen size={18} aria-hidden="true" /> : <PanelLeftClose size={18} aria-hidden="true" />}
          </button>
        )}
      </div>

      <div className="sidebar-scroll">
        <ul className="nav-list">
          <li>{navItem("/", "Accueil", House, undefined, true)}</li>
          <li>
            {navItem(
              "/inbox",
              "À traiter",
              Inbox,
              <CountBadge value={totalUnread} label="alertes non lues" />,
            )}
          </li>
        </ul>

        <div className="nav-section">
          <h2 className="nav-heading">Files</h2>
          {!rail && openCategories.length > 0 && (
            <button type="button" className="link-quiet" onClick={() => saveOpen([])}>
              Tout replier
            </button>
          )}
        </div>
        {workflows.isLoading && <p className="nav-hint" role="status">Chargement des files…</p>}
        {workflows.isError && <p className="nav-hint">Les files n’ont pas pu être chargées.</p>}
        <ul className="nav-list">
          {groups.map(([category, list]) => {
            const open = isOpen(category);
            const unread = list.reduce((sum, workflow) => sum + (workflow.enabled ? unreadOf(workflow) : 0), 0);
            const panelId = `cat-${category.replace(/\W+/g, "-")}`;
            return (
              <li key={category} className="nav-category">
                <button
                  type="button"
                  className="nav-category-button"
                  aria-expanded={open && !rail}
                  aria-controls={panelId}
                  title={rail ? `${category}${unread ? ` — ${unread} non lu${unread > 1 ? "s" : ""}` : ""}` : undefined}
                  onClick={() => {
                    if (rail) {
                      toggle(category, true);
                      onExpandFromRail();
                    } else toggle(category);
                  }}
                  onKeyDown={(event) => onCategoryKey(event, category)}
                >
                  {rail ? (
                    <Folder size={16} aria-hidden="true" />
                  ) : open ? (
                    <ChevronDown size={14} aria-hidden="true" />
                  ) : (
                    <ChevronRight size={14} aria-hidden="true" />
                  )}
                  <span className="nav-label" title={category}>{category}</span>
                  <CountBadge value={unread} tone="neutral" label="non lus" />
                </button>
                {open && !rail && (
                  <ul id={panelId} className="nav-queues">
                    {list.map((workflow) => (
                      <li key={workflow.key}>
                        <NavLink
                          to={`/workflows/${workflow.key}`}
                          className={workflow.enabled ? "nav-queue" : "nav-queue nav-queue-off"}
                          title={workflow.name}
                          onClick={onNavigate}
                        >
                          <span className="nav-label">{workflow.name}</span>
                          {!workflow.enabled && <span className="nav-note">désactivée</span>}
                          {workflow.enabled && unreadOf(workflow) > 0 && (
                            <span className="nav-queue-count" aria-label={`${unreadOf(workflow)} non lus`}>
                              {unreadOf(workflow)}
                            </span>
                          )}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>

        {isAdmin && (
          <>
            <div className="nav-section">
              <h2 className="nav-heading">Administration</h2>
            </div>
            <ul className="nav-list">
              <li>
                {navItem(
                  "/admin/session",
                  "Connexion OmegaFlow",
                  PlugZap,
                  sync.level === "expired" ? <span className="nav-state nav-state-danger">Expirée</span> : null,
                )}
              </li>
              <li>{navItem("/admin/health", "Suivi des lectures", Activity)}</li>
              <li>{navItem("/admin/workflows", "Configuration des files", SlidersHorizontal)}</li>
              <li>{navItem("/admin/users", "Utilisateurs", Users)}</li>
            </ul>
          </>
        )}
      </div>

      <div className="sidebar-user">
        <span className="avatar" aria-hidden="true">{initials(name)}</span>
        <div className="user-text">
          <span className="user-name">{name}</span>
          <span className="user-role">{ROLE_LABELS[user?.role ?? ""] ?? user?.role}</span>
        </div>
        {rail ? (
          <button
            type="button"
            className="icon-btn"
            aria-label={`Thème : ${themeStep.label} (changer)`}
            title={`Thème : ${themeStep.label}`}
            onClick={() => setPreference(themeStep.next)}
          >
            <ThemeIcon size={16} aria-hidden="true" />
          </button>
        ) : null}
        <button type="button" className="icon-btn" aria-label="Se déconnecter" title="Se déconnecter" onClick={() => void logout()}>
          <LogOut size={16} aria-hidden="true" />
        </button>
        {!rail && (
          <div className="user-theme">
            <ThemeSelector />
          </div>
        )}
      </div>
    </nav>
  );
}
