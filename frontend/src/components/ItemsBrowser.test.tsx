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
    if (call.path.startsWith("/inbox")) return { json: makePage(items) };
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

    await userEvent.selectOptions(screen.getByLabelText("Alertes"), "arrival");

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

  it("shows the workflow's rules badge and class on each row", async () => {
    api();
    renderInbox();
    await screen.findByText("D-100");
    expect(screen.getAllByText("À valider sur site").length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("class-ACTION").length).toBeGreaterThan(0);
  });
});
