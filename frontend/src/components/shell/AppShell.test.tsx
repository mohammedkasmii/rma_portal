import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../App";
import { admin, employee, installFetch, makeWorkflow, renderApp } from "../../test/utils";

const session = (state: string) => ({
  state,
  label: state,
  connect_url: null,
  last_error: null,
  last_poll_at: "2026-09-25T07:14:00Z",
  last_success_at: "2026-09-25T07:14:00Z",
  syncing: false,
});

function server(user: typeof employee, state = "READY") {
  return installFetch((call) => {
    if (call.path === "/auth/me") return { json: user };
    if (call.path === "/workflows") return { json: [makeWorkflow()] };
    if (call.path === "/dashboard") {
      return {
        json: {
          activity: [],
          alerts: [],
          counters: { actionable_new: 0, active_total: 0, by_work_status: {}, changed_unread: 0, problem_workflows: 0 },
          last_run: null,
          session: session(state),
          workflows: [makeWorkflow()],
        },
      };
    }
    if (call.path.startsWith("/inbox")) {
      return { json: { items: [], total: 0, page: 1, page_size: 25, facets: { portal_statuses: [], procedures: [], workflows: [] }, columns: [] } };
    }
    return { json: {} };
  });
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("application shell", () => {
  it("defaults to the light theme and remembers a dark choice", async () => {
    server(employee);
    renderApp(<App />, "/inbox");

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    await userEvent.click(await screen.findByRole("radio", { name: "Sombre" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("rma-theme")).toBe("dark");
  });

  it("keeps queue categories collapsed until opened and remembers the choice per user", async () => {
    server(employee);
    renderApp(<App />, "/inbox");

    const category = await screen.findByRole("button", { name: /Devis et photos/ });
    expect(category).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: /Dossiers en instance Photos/ })).not.toBeInTheDocument();

    await userEvent.click(category);
    expect(await screen.findByRole("link", { name: /Dossiers en instance Photos/ })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(`rma-nav-open-${employee.id}`) ?? "[]")).toEqual(["Devis et photos"]);
  });

  it("shows employees a neutral warning without action when the OmegaFlow session expired", async () => {
    server(employee, "AUTH_REQUIRED");
    renderApp(<App />, "/inbox");

    const notice = await screen.findByText(/Les données ne sont pas actualisées actuellement/);
    expect(notice.closest("[role=status]")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Reconnecter" })).not.toBeInTheDocument();
  });

  it("shows administrators a persistent alert with the reconnect action", async () => {
    server(admin, "AUTH_REQUIRED");
    renderApp(<App />, "/inbox");

    expect(await screen.findByRole("alert")).toHaveTextContent("Session OmegaFlow expirée");
    expect(screen.getByRole("link", { name: "Reconnecter" })).toHaveAttribute("href", "/admin/session");
    expect(screen.getByText("Expirée")).toBeInTheDocument();
  });

  it("collapses the sidebar to a rail on request", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("min-width"),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    server(employee);
    renderApp(<App />, "/inbox");

    await userEvent.click(await screen.findByRole("button", { name: "Réduire la navigation" }));
    expect(screen.getByRole("button", { name: "Développer la navigation" })).toHaveAttribute("aria-expanded", "false");
    expect(localStorage.getItem("rma-sidebar")).toBe("rail");
  });
});
