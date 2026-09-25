import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installFetch, makeMembership, renderApp } from "../test/utils";
import { AssistantPanel } from "./AssistantPanel";

afterEach(() => vi.unstubAllGlobals());

const enabled = { enabled: true, model: "qwen3:8b", healthy: true };
const disclaimer = "Suggestion générée automatiquement : elle ne remplace pas le jugement de l'équipe.";

describe("AssistantPanel", () => {
  it("renders nothing when the assistant is disabled or the status call fails", async () => {
    const calls = installFetch((call) =>
      call.path === "/ai/status" ? { json: { enabled: false, model: null, healthy: null } } : undefined,
    );
    renderApp(<AssistantPanel scope={{ kind: "dashboard" }} />);

    await waitFor(() => expect(calls.some((c) => c.path === "/ai/status")).toBe(true));
    expect(screen.queryByLabelText("Assistant local")).not.toBeInTheDocument();
  });

  it("shows a structured summary with its facts and the disclaimer", async () => {
    installFetch((call) => {
      if (call.path === "/ai/status") return { json: enabled };
      if (call.path === "/ai/daily-summary" && call.method === "POST") {
        return {
          json: {
            feature: "DAILY_SUMMARY",
            status: "OK",
            result: { headline: "Journée calme.", highlights: ["3 dossiers actifs"], attention: [] },
            model: "qwen3:8b",
            cached: false,
            disclaimer,
            facts: ["Photos: 3 actifs"],
          },
        };
      }
      return undefined;
    });
    renderApp(<AssistantPanel scope={{ kind: "dashboard" }} />);

    await userEvent.click(await screen.findByText(/Assistant local/));
    await userEvent.click(screen.getByRole("button", { name: "Résumé de la journée" }));

    expect(await screen.findByText("Journée calme.")).toBeInTheDocument();
    expect(screen.getByText("3 dossiers actifs")).toBeInTheDocument();
    expect(screen.getByText("Photos: 3 actifs")).toBeInTheDocument();
    expect(screen.getByText(disclaimer)).toBeInTheDocument();
  });

  it("presents an unavailable assistant as a neutral message, never an error page", async () => {
    installFetch((call) => {
      if (call.path === "/ai/status") return { json: enabled };
      if (call.method === "POST") {
        return {
          json: {
            feature: "DOSSIER_SUMMARY",
            status: "TIMEOUT",
            result: null,
            message: "L'assistant local n'a pas répondu à temps.",
            cached: false,
            disclaimer,
            facts: [],
          },
        };
      }
      return undefined;
    });
    renderApp(
      <>
        <p>Contenu du dossier</p>
        <AssistantPanel scope={{ kind: "dossier", dossierId: 10, memberships: [makeMembership()] }} />
      </>,
    );

    await userEvent.click(await screen.findByText(/Assistant local/));
    await userEvent.click(screen.getByRole("button", { name: "Résumer ce dossier" }));

    expect(await screen.findByText("L'assistant local n'a pas répondu à temps.")).toBeInTheDocument();
    expect(screen.getByText("Contenu du dossier")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("only ever POSTs advisory endpoints (it has no acknowledge, status or note action)", async () => {
    const calls = installFetch((call) => {
      if (call.path === "/ai/status") return { json: enabled };
      return { json: { feature: "PRIORITY_SUGGESTION", status: "OK", result: { priority: "HAUTE", next_action: "Ouvrir.", rationale: "Nouveau." }, cached: false, disclaimer, facts: [] } };
    });
    renderApp(<AssistantPanel scope={{ kind: "dossier", dossierId: 10, memberships: [makeMembership()] }} />);

    await userEvent.click(await screen.findByText(/Assistant local/));
    await userEvent.click(screen.getByRole("button", { name: "Suggérer une priorité" }));
    await screen.findByText(/Priorité suggérée/);

    const posts = calls.filter((c) => c.method === "POST").map((c) => c.path);
    expect(posts).toEqual(["/memberships/55/ai/priority"]);
  });
});
