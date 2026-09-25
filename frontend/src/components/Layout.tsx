import { useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useWorkflows } from "../api/hooks";
import type { WorkflowView } from "../api/types";
import { useAuth } from "../auth";
import { RulesBadge } from "./Badges";

function WorkflowLink({ workflow }: { workflow: WorkflowView }) {
  const unread = workflow.unread_new + workflow.unread_changed;
  return (
    <NavLink to={`/workflows/${workflow.key}`} className={workflow.enabled ? undefined : "nav-disabled"}>
      <span className="nav-label">
        {workflow.name}
        {!workflow.enabled && <small> (désactivé)</small>}
      </span>
      {workflow.needs_site_validation && <RulesBadge value={workflow.rules_status} />}
      {unread > 0 && (
        <span className="count" aria-label={`${unread} alerte${unread > 1 ? "s" : ""} non lue${unread > 1 ? "s" : ""}`}>
          {unread}
        </span>
      )}
    </NavLink>
  );
}

export function Layout() {
  const { user, isAdmin, logout } = useAuth();
  const workflows = useWorkflows();
  const location = useLocation();
  // The drawer is tied to the page it was opened on, so navigating closes it without an effect.
  const [openOn, setOpenOn] = useState<string | null>(null);
  const open = openOn === location.pathname;

  const groups = useMemo(() => {
    const map = new Map<string, WorkflowView[]>();
    for (const workflow of workflows.data ?? []) {
      if (!workflow.enabled && !isAdmin) continue; // disabled queues are only visible to administrators
      map.set(workflow.category, [...(map.get(workflow.category) ?? []), workflow]);
    }
    return [...map.entries()];
  }, [workflows.data, isAdmin]);

  const totalUnread = (workflows.data ?? [])
    .filter((w) => w.enabled)
    .reduce((sum, w) => sum + w.unread_new + w.unread_changed, 0);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Aller au contenu
      </a>
      <header className="topbar">
        <button
          type="button"
          className="menu-button"
          aria-expanded={open}
          aria-controls="sidebar"
          onClick={() => setOpenOn(open ? null : location.pathname)}
        >
          <span aria-hidden="true">☰</span> Menu
        </button>
        <Link to="/" className="brand">
          Portail RMA
        </Link>
        <span className="spacer" />
        <span className="who">{user?.display_name}</span>
        <button type="button" onClick={() => void logout()}>
          Se déconnecter
        </button>
      </header>
      <nav id="sidebar" className={open ? "sidebar open" : "sidebar"} aria-label="Navigation principale">
        <ul className="nav-main">
          <li>
            <NavLink to="/" end>
              Tableau de bord
            </NavLink>
          </li>
          <li>
            <NavLink to="/inbox">
              Boîte de travail
              {totalUnread > 0 && (
                <span className="count" aria-label={`${totalUnread} alertes non lues`}>
                  {totalUnread}
                </span>
              )}
            </NavLink>
          </li>
        </ul>
        {groups.map(([category, list]) => (
          <section key={category} aria-labelledby={`cat-${category}`}>
            <h2 id={`cat-${category}`} className="nav-heading">
              {category}
            </h2>
            <ul>
              {list.map((workflow) => (
                <li key={workflow.key}>
                  <WorkflowLink workflow={workflow} />
                </li>
              ))}
            </ul>
          </section>
        ))}
        {isAdmin && (
          <section aria-labelledby="cat-admin">
            <h2 id="cat-admin" className="nav-heading">
              Administration
            </h2>
            <ul>
              <li><NavLink to="/admin/session">Session OmegaFlow</NavLink></li>
              <li><NavLink to="/admin/health">Santé des workflows</NavLink></li>
              <li><NavLink to="/admin/workflows">Configuration</NavLink></li>
              <li><NavLink to="/admin/users">Utilisateurs</NavLink></li>
            </ul>
          </section>
        )}
      </nav>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
