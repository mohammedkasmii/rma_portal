import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { admin, employee, installFetch, makeItem, makeMembership, makePage, makeWorkflow, renderApp, type Call } from "./test/utils";

afterEach(() => vi.unstubAllGlobals());

const disabledQueue = makeWorkflow({ key: "old", name: "File désactivée", enabled: false, unread_new: 0 });

function server(user: typeof employee | null, extra?: (call: Call) => ReturnType<Parameters<typeof installFetch>[0]>) {
  return installFetch((call) => {
    if (call.path === "/auth/me") return user ? { json: user } : { status: 401, json: { detail: "Authentification requise." } };
    if (call.path === "/workflows") return { json: [makeWorkflow(), disabledQueue] };
    if (call.path === "/admin/users") return { json: [{ ...admin, active: true }] };
    if (call.path.startsWith("/inbox")) return { json: makePage([]) };
    return extra?.(call);
  });
}

describe("routing and roles", () => {
  it("sends an anonymous visitor to the login form", async () => {
    server(null);
    renderApp(<App />, "/");
    expect(await screen.findByRole("heading", { name: "Portail RMA" })).toBeInTheDocument();
    expect(screen.getByLabelText("Identifiant")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
  });

  it("refuses the admin pages to an employee and hides their menu entries", async () => {
    server(employee, () => ({ json: {} }));
    renderApp(<App />, "/admin/users");

    expect(await screen.findByText("Cette page est réservée aux administrateurs.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Utilisateurs" })).not.toBeInTheDocument();
    expect(screen.queryByText("Administration")).not.toBeInTheDocument();
  });

  it("lets an administrator in, and shows disabled queues only to them", async () => {
    server(admin);
    renderApp(<App />, "/admin/users");

    expect(await screen.findByRole("heading", { name: "Utilisateurs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Session OmegaFlow/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /File désactivée/ })).toBeInTheDocument();
  });

  it("hides disabled workflows from employees and shows the validation badge in the navigation", async () => {
    server(employee, () => ({ json: {} }));
    renderApp(<App />, "/inbox");

    await screen.findByRole("link", { name: /Dossiers en instance Photos/ });
    expect(screen.queryByRole("link", { name: /File désactivée/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("À valider sur site").length).toBeGreaterThan(0);
  });

  it("returns to the login page when the API answers 401 mid-session", async () => {
    let expired = false;
    installFetch((call) => {
      if (call.path === "/auth/me") return { json: employee };
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path === "/dashboard") {
        expired = true;
        return { status: 401, json: { detail: "Authentification requise." } };
      }
      return undefined;
    });
    renderApp(<App />, "/");

    await waitFor(() => expect(expired).toBe(true));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/login"));
  });
});

describe("login form", () => {
  it("submits credentials, then lands on the dashboard", async () => {
    let signedIn = false;
    const calls = installFetch((call) => {
      if (call.path === "/auth/me") return signedIn ? { json: employee } : { status: 401, json: { detail: "x" } };
      if (call.path === "/auth/login") {
        signedIn = true;
        return { json: employee };
      }
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path === "/dashboard") return { status: 500, json: { detail: "n/a" } };
      return undefined;
    });
    renderApp(<App />, "/login");

    await userEvent.type(await screen.findByLabelText("Identifiant"), "alice");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "AlicePassword1");
    await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/));
    expect(calls.find((c) => c.path === "/auth/login")?.body).toEqual({ username: "alice", password: "AlicePassword1" });
  });

  it("shows the API's message on a wrong password and stays on the form", async () => {
    installFetch((call) => {
      if (call.path === "/auth/me") return { status: 401, json: { detail: "x" } };
      if (call.path === "/auth/login") return { status: 401, json: { detail: "Identifiants invalides ou compte désactivé." } };
      return undefined;
    });
    renderApp(<App />, "/login");

    await userEvent.type(await screen.findByLabelText("Identifiant"), "alice");
    await userEvent.type(screen.getByLabelText("Mot de passe"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Identifiants invalides");
    expect(screen.getByTestId("location")).toHaveTextContent("/login");
  });
});

describe("dossier page acknowledgement", () => {
  function dossierServer() {
    return installFetch((call) => {
      if (call.path === "/auth/me") return { json: employee };
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path === "/dossiers/10") {
        return {
          json: {
            dossier: {
              id: 10, record_id: "rec1", dossier_number: "D-100", insured_name: "Sara Test", procedure: "", registration: "1-A-1",
              garage: "", portal_status: "", city: "", observation_count: "", estimate_amount_raw: "", active: true,
              first_seen_at: "2026-09-25T08:00:00Z", last_seen_at: "2026-09-25T09:00:00Z", detail_error: null, dates: [],
            },
            memberships: [makeMembership()],
            events: [],
            notes: [],
          },
        };
      }
      if (call.path.includes("/acknowledge")) return { json: { acknowledged: 1 } };
      return undefined;
    });
  }

  it("reading a dossier page by direct link does not acknowledge anything", async () => {
    const calls = dossierServer();
    renderApp(<App />, "/dossiers/10");

    await screen.findByRole("heading", { name: /Dossier D-100/ });

    expect(calls.some((c) => c.path.includes("/acknowledge"))).toBe(false);
  });

  it("opening it from a list row (membership in the URL) acknowledges the unread occurrence once", async () => {
    const calls = dossierServer();
    renderApp(<App />, "/dossiers/10?membership=55");

    await screen.findByRole("heading", { name: /Dossier D-100/ });

    await waitFor(() => expect(calls.filter((c) => c.path === "/occurrences/77/acknowledge")).toHaveLength(1));
  });

  it("selecting a membership tab is the explicit open that acknowledges it", async () => {
    const calls = dossierServer();
    renderApp(<App />, "/dossiers/10");
    const tab = await screen.findByRole("tab", { name: /Dossiers en instance Photos/ });

    await userEvent.click(tab);

    await waitFor(() => expect(calls.some((c) => c.path === "/occurrences/77/acknowledge")).toBe(true));
  });

  it("links to OmegaFlow in a new tab without leaking the opener", async () => {
    dossierServer();
    renderApp(<App />, "/dossiers/10");
    const link = await screen.findByRole("link", { name: /Ouvrir dans OmegaFlow/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(makeItem().omegaflow_url).toContain("omegaflow.example");
  });
});
