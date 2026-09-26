import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { admin, installFetch, makeWorkflow, renderApp, type Call } from "../test/utils";

const session = {
  state: "AUTH_REQUIRED",
  label: "Reconnexion requise",
  connect_url: "https://vnc.example/connect",
  last_error: "Session expirée",
  last_poll_at: "2026-09-25T08:00:00Z",
  last_success_at: "2026-09-25T07:14:00Z",
  syncing: false,
};

const run = (overrides: Record<string, unknown> = {}) => ({
  id: 1, started_at: "2026-09-25T08:00:00Z", completed_at: "2026-09-25T08:03:00Z", status: "PARTIAL", trigger: "SCHEDULED", error: null,
  workflows_total: 3, workflows_complete: 1, workflows_partial: 1, workflows_failed: 1, workflows_auth_required: 0, ...overrides,
});

const health = {
  alerts: [],
  last_run: run(),
  outbox_pending: 2,
  pending_sync_requests: 1,
  recent_runs: [run()],
  session,
  workflows: [
    makeWorkflow({ key: "ok", name: "File saine", last_poll_status: "COMPLETE" }),
    makeWorkflow({ key: "ko", name: "File en échec", last_poll_status: "FAILED", last_error: "Délai dépassé" }),
    makeWorkflow({ key: "part", name: "File partielle", last_poll_status: "PARTIAL" }),
  ],
};

function server(extra?: (call: Call) => ReturnType<Parameters<typeof installFetch>[0]>) {
  return installFetch((call) => {
    if (call.path === "/auth/me") return { json: admin };
    if (call.path === "/workflows") return { json: [makeWorkflow()] };
    if (call.path === "/dashboard") return { status: 500, json: { detail: "n/a" } };
    if (call.path === "/sync") return { json: health };
    if (call.path === "/sync/run") return { json: { queued: true } };
    return extra?.(call);
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("Connexion OmegaFlow", () => {
  it("starts the private login browser before opening noVNC", async () => {
    const replace = vi.fn();
    const popup = { location: { replace }, close: vi.fn(), opener: window };
    vi.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
    const calls = server((call) => {
      if (call.path === "/admin/session/connect") {
        return { status: 202, json: { started: true, state: "started", connect_url: session.connect_url } };
      }
    });
    renderApp(<App />, "/admin/session");

    await userEvent.click(await screen.findByRole("button", { name: /Se connecter \/ Reconnecter/ }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("https://vnc.example/connect"));
    expect(calls.some((c) => c.method === "POST" && c.path === "/admin/session/connect")).toBe(true);
    expect(screen.queryByLabelText(/mot de passe/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Actualiser maintenant" }));
    expect(await screen.findByText("Synchronisation demandée.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/sync/run")).toBe(true);
  });

  it("closes the blank tab and shows a French error when the startup fails", async () => {
    const popup = { location: { replace: vi.fn() }, close: vi.fn(), opener: window };
    vi.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
    server((call) => {
      if (call.path === "/admin/session/connect") return { status: 503, json: { detail: "hors ligne" } };
    });
    renderApp(<App />, "/admin/session");

    await userEvent.click(await screen.findByRole("button", { name: /Se connecter \/ Reconnecter/ }));

    expect(await screen.findByText(/Impossible de démarrer la connexion OmegaFlow/)).toBeInTheDocument();
    expect(popup.close).toHaveBeenCalled();
    expect(popup.location.replace).not.toHaveBeenCalled();
  });

  it("explains that a login is already active and still opens the desktop", async () => {
    const replace = vi.fn();
    vi.spyOn(window, "open").mockReturnValue({ location: { replace }, close: vi.fn(), opener: window } as unknown as Window);
    server((call) => {
      if (call.path === "/admin/session/connect") {
        return { status: 202, json: { started: false, state: "already_running", connect_url: session.connect_url } };
      }
    });
    renderApp(<App />, "/admin/session");

    await userEvent.click(await screen.findByRole("button", { name: /Se connecter \/ Reconnecter/ }));

    expect(await screen.findByText(/déjà ouverte/)).toBeInTheDocument();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("https://vnc.example/connect"));
  });
});

describe("Suivi des lectures", () => {
  it("lists errors first and filters by state", async () => {
    server();
    renderApp(<App />, "/admin/health");

    const table = (await screen.findByRole("region", { name: "État par file" })).querySelector("tbody")!;
    const names = within(table).getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(names[0]).toContain("File en échec");
    expect(names[1]).toContain("File partielle");
    expect(names[2]).toContain("File saine");

    await userEvent.click(screen.getByRole("button", { name: /^À surveiller/ }));
    expect(within(table).queryByText("File saine")).not.toBeInTheDocument();
    expect(screen.getByText("Délai dépassé")).toBeInTheDocument();
  });
});

describe("Configuration des files", () => {
  it("saves a change with the existing PATCH endpoint and confirms it", async () => {
    const calls = server((call) => {
      if (call.path === "/admin/workflows" && call.method === "GET") return { json: [makeWorkflow(), makeWorkflow({ key: "other", name: "Autre file", category: "Rapports" })] };
      if (call.path.startsWith("/admin/workflows/")) return { json: makeWorkflow() };
      return undefined;
    });
    renderApp(<App />, "/admin/workflows");

    await userEvent.selectOptions(await screen.findByLabelText("Règle de Dossiers en instance Photos"), "CONFIRMED");

    expect(await screen.findByText("Configuration enregistrée.")).toBeInTheDocument();
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.path).toBe("/admin/workflows/photos_pending");
    expect(patch?.body).toEqual({ rules_status: "CONFIRMED" });
    expect(screen.getByRole("columnheader", { name: "Règle" })).toBeInTheDocument();
    expect(screen.getByText("Rapports")).toBeInTheDocument(); // category grouping

    await userEvent.type(screen.getByRole("searchbox", { name: "Filtrer les files" }), "autre");
    expect(screen.queryByText("Dossiers en instance Photos")).not.toBeInTheDocument();
  });
});

describe("Utilisateurs", () => {
  const users = [
    { ...admin, active: true },
    { id: 2, username: "alice", display_name: "Alice", role: "EMPLOYEE", active: true },
  ];

  it("creates a user from the add form and confirms", async () => {
    const calls = server((call) => {
      if (call.path === "/admin/users" && call.method === "GET") return { json: users };
      if (call.path === "/admin/users" && call.method === "POST") return { json: { id: 3, username: "bob", display_name: "Bob", role: "EMPLOYEE", active: true } };
      return undefined;
    });
    renderApp(<App />, "/admin/users");

    await userEvent.click(await screen.findByRole("button", { name: "Ajouter un utilisateur" }));
    await userEvent.type(screen.getByLabelText("Identifiant"), "bob");
    await userEvent.type(screen.getByLabelText("Nom affiché"), "Bob");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "MotDePasse1234");
    await userEvent.click(screen.getByRole("button", { name: "Créer" }));

    expect(await screen.findByText("Utilisateur créé.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/admin/users")?.body).toEqual({
      username: "bob", display_name: "Bob", password: "MotDePasse1234", role: "EMPLOYEE",
    });
  });

  it("deactivates another user but never the signed-in administrator", async () => {
    const calls = server((call) => {
      if (call.path === "/admin/users" && call.method === "GET") return { json: users };
      if (call.path === "/admin/users/2") return { json: { ...users[1], active: false } };
      return undefined;
    });
    renderApp(<App />, "/admin/users");

    expect(await screen.findByRole("button", { name: /Désactiver admin/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Désactiver alice/ }));

    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ active: false }));
    expect(await screen.findByText("alice désactivé.")).toBeInTheDocument();
  });
});
