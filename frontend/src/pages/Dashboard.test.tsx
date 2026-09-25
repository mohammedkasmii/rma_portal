import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { admin, employee, installFetch, makeItem, makePage, makeWorkflow, renderApp } from "../test/utils";

const session = {
  state: "READY",
  label: "Session OmegaFlow active",
  connect_url: null,
  last_error: null,
  last_poll_at: "2026-09-25T08:00:00Z",
  last_success_at: "2026-09-25T08:00:00Z",
  syncing: false,
};

function dashboard(overrides: Record<string, unknown> = {}) {
  return {
    activity: [],
    alerts: [],
    counters: { actionable_new: 4, active_total: 30, by_work_status: { TO_DO: 7, IN_PROGRESS: 2 }, changed_unread: 3, problem_workflows: 0 },
    last_run: null,
    session,
    workflows: [makeWorkflow({ unread_new: 2, unread_changed: 1 }), makeWorkflow({ key: "quiet", name: "File calme", unread_new: 0 })],
    ...overrides,
  };
}

function server(user: typeof employee, dash: () => { status?: number; json?: unknown }, unread = makePage([makeItem()])) {
  return installFetch((call) => {
    if (call.path === "/auth/me") return { json: user };
    if (call.path === "/workflows") return { json: [makeWorkflow()] };
    if (call.path === "/dashboard") return dash();
    if (call.path.startsWith("/inbox")) return { json: unread };
    if (call.path === "/occurrences/77/acknowledge") return { json: { acknowledged: 1 } };
    if (call.path.startsWith("/ai/status")) return { json: { enabled: false } };
    return undefined;
  });
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("Accueil", () => {
  it("shows the counters returned by the API and links each one to a pre-filtered À traiter", async () => {
    server(employee, () => ({ json: dashboard() }));
    renderApp(<App />, "/");

    expect(await screen.findByRole("heading", { name: "Bonjour Alice" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "À traiter : 7" })).toHaveAttribute("href", "/inbox?work_status=TO_DO");
    expect(screen.getByRole("link", { name: "Nouveaux : 4" })).toHaveAttribute("href", "/inbox?unread=true&kind=arrival");
    expect(screen.getByRole("link", { name: "Modifications : 3" })).toHaveAttribute("href", "/inbox?unread=true&kind=changed");
    expect(screen.getByRole("link", { name: "En cours : 2" })).toHaveAttribute("href", "/inbox?work_status=IN_PROGRESS");
    expect(screen.getByText(/7 dossiers demandent une action/)).toBeInTheDocument();
  });

  it("lists only queues with unread alerts as the busiest queues", async () => {
    server(employee, () => ({ json: dashboard() }));
    renderApp(<App />, "/");

    const card = (await screen.findByRole("heading", { name: "Files avec le plus d’actions" })).closest("section")!;
    expect(within(card).getByText("Dossiers en instance Photos")).toBeInTheDocument();
    expect(within(card).queryByText("File calme")).not.toBeInTheDocument();
  });

  it("acknowledges an unread change when its row is opened, then opens the dossier", async () => {
    const calls = server(employee, () => ({ json: dashboard() }));
    renderApp(<App />, "/");

    await userEvent.click(await screen.findByRole("button", { name: /D-100/ }));

    expect(calls.some((c) => c.method === "POST" && c.path === "/occurrences/77/acknowledge")).toBe(true);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/dossiers/10?membership=55"));
  });

  it("shows an empty state when nothing is unread", async () => {
    server(employee, () => ({ json: dashboard() }), makePage([]));
    renderApp(<App />, "/");

    expect(await screen.findByText("Aucun changement non lu")).toBeInTheDocument();
  });

  it("warns, without technical detail, when a queue was not read completely", async () => {
    server(employee, () => ({
      json: dashboard({ workflows: [makeWorkflow({ last_poll_status: "PARTIAL" })], counters: { actionable_new: 0, active_total: 0, by_work_status: {}, changed_unread: 0, problem_workflows: 1 } }),
    }));
    renderApp(<App />, "/");

    expect(await screen.findByText(/Certaines données ne sont pas à jour/)).toBeInTheDocument();
    expect(screen.getByText(/Rien à traiter pour le moment/)).toBeInTheDocument();
  });

  it("shows synchronization alerts to administrators only", async () => {
    const alert = {
      id: 1, kind: "SESSION_AUTH_REQUIRED", notification_class: "ACTION", detected_at: "2026-09-25T08:00:00Z", changed_fields: [], changed_labels: [],
      dossier_id: null, dossier_number: null, insured_name: null, membership_id: null, occurrence_id: null, workflow_key: null, workflow_name: null, message: null,
    };
    server(admin, () => ({ json: dashboard({ alerts: [alert] }) }));
    renderApp(<App />, "/");
    expect(await screen.findByRole("alert", { name: "Alertes d’administration" })).toBeInTheDocument();
  });

  it("offers a retry when the dashboard cannot be loaded", async () => {
    let attempts = 0;
    server(employee, () => {
      attempts += 1;
      return attempts < 2 ? { status: 500, json: { detail: "Erreur serveur" } } : { json: dashboard() };
    });
    renderApp(<App />, "/");

    await userEvent.click(await screen.findByRole("button", { name: "Réessayer" }));
    expect(await screen.findByRole("heading", { name: "Bonjour Alice" })).toBeInTheDocument();
  });
});
