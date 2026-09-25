import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { admin, employee, installFetch, makeItem, makePage, makeWorkflow, renderApp } from "../test/utils";

function server(user: typeof employee, status = 200) {
  const workflow = makeWorkflow({ active_count: 9, by_work_status: { TO_DO: 5, DONE: 1 }, last_success_at: "2026-09-25T08:30:00Z" });
  return installFetch((call) => {
    if (call.path === "/auth/me") return { json: user };
    if (call.path === "/workflows") return { json: [workflow] };
    if (call.path === "/dashboard") return { status: 500, json: { detail: "n/a" } };
    if (call.path === "/workflows/photos_pending") {
      if (status === 404) return { status, json: { detail: "inconnu" } };
      return { json: { workflow, columns: [], filter_label: null, primary_date_labels: [], recent_events: [], recent_runs: [] } };
    }
    if (call.path.startsWith("/workflows/photos_pending/items")) return { json: makePage([makeItem()]) };
    return undefined;
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("queue page", () => {
  it("shows the queue header, its category and status tabs from the API counts", async () => {
    server(employee);
    renderApp(<App />, "/workflows/photos_pending");

    expect(await screen.findByRole("heading", { name: /Dossiers en instance Photos/ })).toBeInTheDocument();
    expect(screen.getAllByText("Devis et photos").length).toBeGreaterThan(0);
    expect(await screen.findByRole("tab", { name: /Tous/ })).toHaveTextContent("9");
    expect(screen.getByRole("tab", { name: /À traiter/ })).toHaveTextContent("5");
    expect(screen.getByText(/Dernière actualisation : /)).toBeInTheDocument();
  });

  it("states the provisional rule once for the page, in employee wording", async () => {
    server(employee);
    renderApp(<App />, "/workflows/photos_pending");

    expect(await screen.findByRole("note")).toHaveTextContent("règle provisoire");
    expect(screen.queryByText("À valider sur site")).not.toBeInTheDocument();
    expect(screen.queryByText(/Détails de lecture/)).not.toBeInTheDocument();
  });

  it("gives administrators the reading details that employees do not see", async () => {
    server(admin);
    renderApp(<App />, "/workflows/photos_pending");

    expect(await screen.findByText(/Détails de lecture/)).toBeInTheDocument();
  });

  it("says so when the queue does not exist", async () => {
    server(employee, 404);
    renderApp(<App />, "/workflows/photos_pending");

    expect(await screen.findByText("Ce workflow n’existe pas.")).toBeInTheDocument();
  });
});
