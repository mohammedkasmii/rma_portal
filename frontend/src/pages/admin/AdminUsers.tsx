import { ListFilter, UserPlus } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { useCreateUser, useSetUserActive, useUsers } from "../../api/hooks";
import type { AdminUser } from "../../api/types";
import { useAuth } from "../../auth";
import { Button } from "../../components/ui/Button";
import { FilterChip } from "../../components/ui/Filters";
import { SelectField, TextField } from "../../components/ui/Form";
import { PageHeader } from "../../components/ui/PageHeader";
import { DelayedSkeleton, EmptyState, ErrorState } from "../../components/ui/States";
import { useToast } from "../../components/ui/Toast";

type Filter = "all" | "admin" | "employee" | "inactive";
const EMPTY_FORM = { username: "", display_name: "", password: "", role: "EMPLOYEE" };

export function AdminUsers() {
  const { user: me } = useAuth();
  const users = useUsers();
  const create = useCreateUser();
  const setActive = useSetUserActive();
  const toast = useToast();
  const [form, setForm] = useState(EMPTY_FORM);
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [needle, setNeedle] = useState("");

  const all = useMemo<AdminUser[]>(() => users.data ?? [], [users.data]);
  const rows = useMemo(() => {
    const text = needle.trim().toLocaleLowerCase("fr");
    return all.filter((u) => {
      if (filter === "admin" && u.role !== "ADMIN") return false;
      if (filter === "employee" && u.role === "ADMIN") return false;
      if (filter === "inactive" && u.active) return false;
      return !text || `${u.display_name} ${u.username}`.toLocaleLowerCase("fr").includes(text);
    });
  }, [all, filter, needle]);

  if (users.isLoading) {
    return (
      <>
        <PageHeader title="Utilisateurs" />
        <DelayedSkeleton rows={6} label="Chargement des utilisateurs…" />
      </>
    );
  }
  if (!users.data) {
    return (
      <>
        <PageHeader title="Utilisateurs" />
        <ErrorState message={users.error?.message} onRetry={() => void users.refetch()} />
      </>
    );
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(form, {
      onSuccess: () => {
        setForm(EMPTY_FORM);
        setAdding(false);
        toast({ message: "Utilisateur créé." });
      },
    });
  };
  const toggleActive = (u: AdminUser) =>
    setActive.mutate(
      { id: u.id, active: !u.active },
      {
        onSuccess: () => toast({ message: u.active ? `${u.username} désactivé.` : `${u.username} réactivé.` }),
        onError: (error) => toast({ tone: "error", message: error.message }),
      },
    );

  return (
    <>
      <PageHeader
        title="Utilisateurs"
        count={all.length}
        description="Accès au portail. Les comptes OmegaFlow individuels ne sont pas gérés ici."
        actions={
          <Button variant="primary" icon={<UserPlus size={15} aria-hidden="true" />} aria-expanded={adding} onClick={() => setAdding((open) => !open)}>
            Ajouter un utilisateur
          </Button>
        }
      />

      {adding && (
        <form className="card user-form" onSubmit={submit} aria-label="Nouvel utilisateur">
          <div className="card-head">
            <h2>Nouvel utilisateur</h2>
          </div>
          <div className="user-form-grid">
            <TextField label="Identifiant" required minLength={3} autoComplete="off" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            <TextField label="Nom affiché" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            <TextField
              label="Mot de passe"
              type="password"
              required
              autoComplete="new-password"
              hint="10 caractères minimum."
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
            <SelectField label="Rôle" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="EMPLOYEE">Employé</option>
              <option value="ADMIN">Administrateur</option>
            </SelectField>
          </div>
          <div className="user-form-foot">
            <Button type="submit" variant="primary" loading={create.isPending}>Créer</Button>
            <Button variant="ghost" onClick={() => setAdding(false)}>Annuler</Button>
            <span role="status" aria-live="polite" className="error-text">{create.isError && create.error.message}</span>
          </div>
        </form>
      )}

      <div className="filter-bar">
        <div className="filter-input">
          <ListFilter size={15} aria-hidden="true" />
          <input type="search" aria-label="Filtrer les utilisateurs" placeholder="Filtrer les utilisateurs…" value={needle} onChange={(e) => setNeedle(e.target.value)} />
        </div>
        <FilterChip selected={filter === "all"} onSelect={() => setFilter("all")} count={all.length}>Tous</FilterChip>
        <FilterChip selected={filter === "admin"} onSelect={() => setFilter("admin")} count={all.filter((u) => u.role === "ADMIN").length}>Administrateurs</FilterChip>
        <FilterChip selected={filter === "employee"} onSelect={() => setFilter("employee")} count={all.filter((u) => u.role !== "ADMIN").length}>Employés</FilterChip>
        <FilterChip selected={filter === "inactive"} onSelect={() => setFilter("inactive")} count={all.filter((u) => !u.active).length}>Désactivés</FilterChip>
      </div>
      <p role="status" aria-live="polite" className="error-text">{setActive.isError && setActive.error.message}</p>

      <div className="card list-card">
        {rows.length === 0 ? (
          <EmptyState title="Aucun utilisateur ne correspond" />
        ) : (
          <div className="table-wrap" role="region" aria-label="Utilisateurs" tabIndex={0}>
            <table className="admin-table">
              <caption className="visually-hidden">Comptes locaux</caption>
              <thead>
                <tr>
                  <th scope="col">Utilisateur</th>
                  <th scope="col">Rôle</th>
                  <th scope="col">État</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => (
                  <tr key={u.id}>
                    <th scope="row">
                      <span className="stack">
                        <span>{u.display_name}</span>
                        <span className="row-sub mono">{u.username}</span>
                      </span>
                    </th>
                    <td>
                      <span className={u.role === "ADMIN" ? "badge badge-class-informational" : "badge badge-neutral"}>
                        {u.role === "ADMIN" ? "Administrateur" : "Employé"}
                      </span>
                    </td>
                    <td>
                      <span className={u.active ? "badge badge-ok" : "badge badge-neutral"}>{u.active ? "Actif" : "Désactivé"}</span>
                    </td>
                    <td>
                      <Button
                        disabled={u.id === me?.id}
                        title={u.id === me?.id ? "Vous ne pouvez pas désactiver votre propre compte" : undefined}
                        onClick={() => toggleActive(u)}
                      >
                        {u.active ? "Désactiver" : "Réactiver"}{" "}
                        <span className="visually-hidden">{u.username}</span>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
