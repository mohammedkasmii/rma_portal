import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { employee, installFetch, makeMembership, makeWorkflow, renderApp, type Call } from "../test/utils";

const dossier = {
  id: 10,
  record_id: "rec1",
  dossier_number: "D-100",
  insured_name: "Sara Test",
  procedure: "Garage agréé",
  registration: "1-A-1",
  garage: "Garage Test",
  portal_status: "Attente photos",
  city: "Ville Test",
  observation_count: "2",
  estimate_amount_raw: "",
  active: true,
  first_seen_at: "2026-09-25T08:00:00Z",
  last_seen_at: "2026-09-25T09:00:00Z",
  detail_error: null,
  dates: [{ key: "d1", label: "Date de sinistre", value: "01/09/2026", date: true }],
};

const notes = [
  { id: 1, author: "Bob", body: "Première note", created_at: "2026-09-24T10:00:00Z", membership_id: null, workflow_name: null },
  { id: 2, author: "Alice", body: "Note récente", created_at: "2026-09-25T10:00:00Z", membership_id: 55, workflow_name: "Dossiers en instance Photos" },
];

function server(extra?: (call: Call) => ReturnType<Parameters<typeof installFetch>[0]>) {
  return installFetch((call) => {
    if (call.path === "/auth/me") return { json: employee };
    if (call.path === "/workflows") return { json: [makeWorkflow()] };
    if (call.path === "/dashboard") return { status: 500, json: { detail: "n/a" } };
    if (call.path === "/dossiers/10/notes" && call.method === "POST") {
      return { json: { id: 3, author: "Alice", body: "x", created_at: "2026-09-25T11:00:00Z", membership_id: null, workflow_name: null } };
    }
    if (call.path === "/dossiers/10") {
      return {
        json: {
          dossier,
          memberships: [makeMembership({ work_updated_by: "Bob", work_updated_at: "2026-09-25T09:44:00Z", work_status: "IN_PROGRESS", primary_date_raw: "27/09/2026" })],
          events: [
            {
              id: 9, kind: "WORKFLOW_ITEM_CHANGED", notification_class: "ACTION", detected_at: "2026-09-25T09:38:00Z", changed_fields: ["portal_status"],
              changed_labels: ["Statut OmegaFlow"], dossier_id: 10, dossier_number: "D-100", insured_name: "Sara Test", membership_id: 55, occurrence_id: 77,
              workflow_key: "photos_pending", workflow_name: "Dossiers en instance Photos", message: null,
            },
          ],
          notes,
        },
      };
    }
    if (call.path.startsWith("/ai/status")) return { json: { enabled: false } };
    return extra?.(call);
  });
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("dossier page", () => {
  it("shows the identity, the OmegaFlow action and the two statuses separately", async () => {
    server();
    renderApp(<App />, "/dossiers/10");

    expect(await screen.findByRole("heading", { name: "D-100" })).toBeInTheDocument();
    expect(screen.getByText(/Sara Test/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Ouvrir dans OmegaFlow/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByText("Attente photos", { selector: ".tag" })).toBeInTheDocument(); // Statut OmegaFlow
    expect(screen.getByRole("radio", { name: "En cours" })).toBeChecked(); // Statut de traitement
    expect(screen.getByText(/par Bob/)).toBeInTheDocument();
    expect(screen.getByText("27/09/2026")).toBeInTheDocument();
  });

  it("lists what changed from the recorded events, without inventing values", async () => {
    server();
    renderApp(<App />, "/dossiers/10");

    const card = (await screen.findByRole("heading", { name: "Ce qui a changé" })).closest("section")!;
    expect(within(card).getByText("Modification")).toBeInTheDocument();
    expect(within(card).getByText(/Statut OmegaFlow/)).toBeInTheDocument();
  });

  it("shows notes newest first with author and time", async () => {
    server();
    renderApp(<App />, "/dossiers/10");

    const list = (await screen.findByRole("heading", { name: /Notes/ })).closest("section")!;
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Alice");
    expect(items[0]).toHaveTextContent("Note récente");
    expect(items[1]).toHaveTextContent("Bob");
  });

  it("publishes a note with Ctrl+Enter and confirms it", async () => {
    const calls = server();
    renderApp(<App />, "/dossiers/10");

    await userEvent.type(await screen.findByLabelText("Nouvelle note"), "Garage relancé{Control>}{Enter}{/Control}");

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/dossiers/10/notes")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ body: "Garage relancé", membership_id: null });
    expect(await screen.findByText("Note ajoutée.")).toBeInTheDocument();
    expect(screen.getByLabelText("Nouvelle note")).toHaveValue("");
  });

  it("keeps an unsent draft when the page is left and reopened", async () => {
    server();
    const first = renderApp(<App />, "/dossiers/10");
    await userEvent.type(await screen.findByLabelText("Nouvelle note"), "à finir");
    first.unmount();

    renderApp(<App />, "/dossiers/10");
    expect(await screen.findByLabelText("Nouvelle note")).toHaveValue("à finir");
  });

  it("tells the user when a note cannot be saved and keeps the text", async () => {
    installFetch((call) => {
      if (call.path === "/auth/me") return { json: employee };
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path === "/dossiers/10/notes") return { status: 500, json: { detail: "Base indisponible." } };
      if (call.path === "/dossiers/10") return { json: { dossier, memberships: [makeMembership()], events: [], notes: [] } };
      return undefined;
    });
    renderApp(<App />, "/dossiers/10");

    await userEvent.type(await screen.findByLabelText("Nouvelle note"), "important");
    await userEvent.click(screen.getByRole("button", { name: "Ajouter la note" }));

    expect(await screen.findByText("Base indisponible.")).toBeInTheDocument();
    expect(screen.getByLabelText("Nouvelle note")).toHaveValue("important");
  });

  it("offers a retry when the dossier cannot be loaded, and a clear message when it does not exist", async () => {
    installFetch((call) => {
      if (call.path === "/auth/me") return { json: employee };
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path === "/dossiers/10") return { status: 404, json: { detail: "inconnu" } };
      if (call.path === "/dossiers/11") return { status: 500, json: { detail: "Erreur serveur" } };
      return undefined;
    });
    const view = renderApp(<App />, "/dossiers/10");
    expect(await screen.findByText("Ce dossier n’existe pas.")).toBeInTheDocument();
    view.unmount();

    renderApp(<App />, "/dossiers/11");
    expect(await screen.findByRole("button", { name: "Réessayer" })).toBeInTheDocument();
  });
});
