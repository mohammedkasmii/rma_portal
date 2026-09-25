import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { employee, installFetch, makeItem, makePage, makeWorkflow, renderApp, type Call } from "../test/utils";
import { ItemsBrowser } from "./ItemsBrowser";

function api(items = [makeItem(), makeItem({ membership_id: 56, occurrence_id: 78, dossier_id: 11, dossier_number: "D-101", unread: false, unread_kind: null })]) {
  return installFetch((call: Call) => {
    if (call.path === "/auth/me") return { json: employee };
    if (call.path === "/workflows") return { json: [makeWorkflow()] };
    if (call.path.startsWith("/inbox") || call.path.endsWith("/items") || call.path.includes("/items?")) return { json: makePage(items) };
    if (call.method === "POST" && call.path.startsWith("/occurrences/")) return { json: { acknowledged: 1 } };
    return undefined;
  });
}

function renderInbox(route = "/inbox") {
  return renderApp(
    <Routes>
      <Route path="/inbox" element={<ItemsBrowser workflowKey={null} caption="Dossiers" />} />
      <Route path="/dossiers/:id" element={<p>page dossier</p>} />
    </Routes>,
    route,
  );
}

function stubMedia(matching: string) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes(matching),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("ItemsBrowser", () => {
  it("only lists on load: no occurrence is acknowledged by merely showing the inbox", async () => {
    const calls = api();
    renderInbox();

    await screen.findByText("D-100");

    expect(calls.some((c) => c.path.includes("/acknowledge"))).toBe(false);
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("opening an unread row acknowledges its occurrence, then navigates to the dossier", async () => {
    const calls = api();
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /D-100/ }));

    await screen.findByText("page dossier");
    const acks = calls.filter((c) => c.path.includes("/acknowledge"));
    expect(acks).toHaveLength(1);
    expect(acks[0]).toMatchObject({ method: "POST", path: "/occurrences/77/acknowledge" });
    expect(screen.getByTestId("location")).toHaveTextContent("/dossiers/10?membership=55");
  });

  it("opening an already-read row does not call the acknowledge endpoint", async () => {
    const calls = api();
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /D-101/ }));

    await screen.findByText("page dossier");
    expect(calls.some((c) => c.path.includes("/acknowledge"))).toBe(false);
  });

  it("still opens the dossier when the acknowledgement fails", async () => {
    installFetch((call) => {
      if (call.path === "/auth/me") return { json: employee };
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path.startsWith("/inbox")) return { json: makePage([makeItem()]) };
      if (call.path.includes("/acknowledge")) return { status: 500, json: { detail: "boom" } };
      return undefined;
    });
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /D-100/ }));

    await screen.findByText("page dossier");
  });

  it("sends filters to the API and keeps them in the URL", async () => {
    const calls = api();
    renderInbox("/inbox?search=karim&work_status=DONE&page=2");

    await screen.findByText("D-100");
    const first = calls.find((c) => c.path.startsWith("/inbox"))!;
    expect(first.path).toContain("search=karim");
    expect(first.path).toContain("work_status=DONE");
    expect(first.path).toContain("page=2");
    expect(first.path).toContain("page_size=25");

    await userEvent.click(screen.getByRole("button", { name: /^Nouveau/ }));

    await waitFor(() => {
      const url = screen.getByTestId("location").textContent!;
      expect(url).toContain("unread=true");
      expect(url).toContain("kind=arrival");
      expect(url).not.toContain("page="); // changing a filter returns to page 1
    });
    await waitFor(() => {
      const last = calls.filter((c) => c.path.startsWith("/inbox")).at(-1)!;
      expect(last.path).toContain("unread=true");
      expect(last.path).toContain("kind=arrival");
    });
  });

  it("sorts on the server: clicking a header changes sort/order in the URL", async () => {
    api();
    renderInbox();
    await screen.findByText("D-100");

    await userEvent.click(screen.getByRole("button", { name: /Assuré/ }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("sort=name&order=desc"));
    await userEvent.click(screen.getByRole("button", { name: /Assuré/ }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("sort=name&order=asc"));
  });

  it("keeps rows free of the validation badge and the notification class", async () => {
    api();
    renderInbox();
    await screen.findByText("D-100");
    expect(screen.queryByText("À valider sur site")).not.toBeInTheDocument();
    expect(screen.queryByTestId("class-ACTION")).not.toBeInTheDocument();
  });

  it("waits for typing to pause before asking the server (debounced search)", async () => {
    const calls = api();
    renderInbox();
    await screen.findByText("D-100");
    const before = calls.filter((c) => c.path.startsWith("/inbox")).length;

    await userEvent.type(screen.getByRole("searchbox", { name: "Filtrer la liste" }), "karim");

    // Typed in one burst: no request is sent per character.
    expect(calls.filter((c) => c.path.startsWith("/inbox")).length).toBe(before);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("search=karim"));
    const searches = calls.filter((c) => c.path.includes("search="));
    expect(searches.length).toBeGreaterThan(0);
    expect(searches.every((c) => c.path.includes("search=karim"))).toBe(true);
  });

  it("offers quick views that exclude each other and a way to clear every filter", async () => {
    api();
    renderInbox("/inbox?unread=true&kind=changed&work_status=DONE");
    await screen.findByText("D-100");

    expect(screen.getByRole("button", { name: /^Modification/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Nouveau/ })).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(screen.getByRole("button", { name: "Effacer les filtres" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/inbox$/));
    expect(screen.queryByRole("button", { name: "Effacer les filtres" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Tous/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains an empty result caused by filters and lets the user clear them", async () => {
    api([]);
    renderInbox("/inbox?work_status=DONE");

    expect(await screen.findByText("Aucun dossier ne correspond à ces critères")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Effacer les filtres" })[0]);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/inbox$/));
  });

  it("shows a neutral empty state when the list has nothing", async () => {
    api([]);
    renderInbox();
    expect(await screen.findByText("Rien à traiter ici")).toBeInTheDocument();
  });

  it("shows an error state with a retry when the list cannot be loaded", async () => {
    let attempts = 0;
    installFetch((call: Call) => {
      if (call.path === "/auth/me") return { json: employee };
      if (call.path === "/workflows") return { json: [makeWorkflow()] };
      if (call.path.startsWith("/inbox")) {
        attempts += 1;
        return attempts === 1 ? { status: 500, json: { detail: "Le serveur ne répond pas." } } : { json: makePage([makeItem()]) };
      }
      return undefined;
    });
    renderInbox();

    expect(await screen.findByText("Impossible de charger la liste")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(await screen.findByText("D-100")).toBeInTheDocument();
  });

  it("uses dossier cards instead of a table on a phone-sized screen", async () => {
    stubMedia("max-width: 767px");
    api();
    renderInbox();

    await screen.findByText("D-100");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Dossiers" })).toBeInTheDocument();
  });

  it("previews a queue row on wide screens without opening or acknowledging it", async () => {
    stubMedia("min-width: 1280px");
    const calls = api();
    renderApp(
      <Routes>
        <Route path="/workflows/:key" element={<ItemsBrowser workflowKey="photos_pending" caption="File" />} />
      </Routes>,
      "/workflows/photos_pending",
    );

    await userEvent.click(await screen.findByRole("button", { name: "Aperçu du dossier D-100" }));

    const panel = await screen.findByRole("complementary", { name: "Aperçu du dossier" });
    expect(panel).toHaveTextContent("D-100");
    expect(panel).toHaveTextContent("Ouvrir dans OmegaFlow");
    expect(calls.some((c) => c.path.includes("/acknowledge"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Fermer l’aperçu" }));
    expect(screen.queryByRole("complementary", { name: "Aperçu du dossier" })).not.toBeInTheDocument();
  });

  it("shows treatment-status tabs with the queue counts and filters by the selected one", async () => {
    api();
    renderApp(
      <Routes>
        <Route
          path="/workflows/:key"
          element={<ItemsBrowser workflowKey="photos_pending" caption="File" statusCounts={{ counts: { TO_DO: 5, DONE: 1 }, total: 9 }} />}
        />
      </Routes>,
      "/workflows/photos_pending",
    );

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Tous9", "À traiter5", "En cours0", "En attente0", "Terminé1"]);
    await userEvent.click(screen.getByRole("tab", { name: /Terminé/ }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("work_status=DONE"));
    expect(screen.getByRole("tab", { name: /Terminé/ })).toHaveAttribute("aria-selected", "true");
  });
});
