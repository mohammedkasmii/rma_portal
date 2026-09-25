import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { AppShell } from "./components/shell/AppShell";
import { AdminHealth, AdminSession, AdminUsers, AdminWorkflows } from "./pages/Admin";
import { Dashboard } from "./pages/Dashboard";
import { DossierPage } from "./pages/DossierPage";
import { Inbox } from "./pages/Inbox";
import { Login } from "./pages/Login";
import { WorkflowPage } from "./pages/WorkflowPage";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <p className="page-loading">Chargement…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

/** Administration pages are hidden from employees; a direct visit gets an explicit refusal. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  if (!isAdmin) {
    return (
      <>
        <h1>Accès refusé</h1>
        <p role="alert">Cette page est réservée aux administrateurs.</p>
      </>
    );
  }
  return children;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="inbox" element={<Inbox />} />
        <Route path="workflows/:key" element={<WorkflowPage />} />
        <Route path="dossiers/:id" element={<DossierPage />} />
        <Route path="admin/session" element={<RequireAdmin><AdminSession /></RequireAdmin>} />
        <Route path="admin/health" element={<RequireAdmin><AdminHealth /></RequireAdmin>} />
        <Route path="admin/workflows" element={<RequireAdmin><AdminWorkflows /></RequireAdmin>} />
        <Route path="admin/users" element={<RequireAdmin><AdminUsers /></RequireAdmin>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
