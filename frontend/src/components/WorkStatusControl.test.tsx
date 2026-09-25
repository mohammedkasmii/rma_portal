import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installFetch } from "../test/utils";
import { WorkStatusControl } from "./WorkStatusControl";

function setup() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkStatusControl membershipId={55} status="TO_DO" version={3} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("WorkStatusControl", () => {
  it("sends the version with the change (optimistic locking) and confirms", async () => {
    const calls = installFetch(() => ({ json: { membership_id: 55, status: "DONE", version: 4 } }));
    setup();

    await userEvent.selectOptions(screen.getByLabelText("Statut de traitement"), "DONE");

    await waitFor(() => expect(screen.getByText("Statut enregistré.")).toBeInTheDocument());
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/memberships/55/work-status");
    expect(put?.body).toEqual({ status: "DONE", expected_version: 3 });
  });

  it("on a 409 shows the server's current state and who changed it, then retries with the new version", async () => {
    let attempt = 0;
    const calls = installFetch(() => {
      attempt += 1;
      if (attempt === 1) {
        return {
          status: 409,
          json: {
            detail: "Ce statut a été modifié entre-temps.",
            current: { membership_id: 55, status: "WAITING", version: 5, updated_by: "Bob", updated_at: "2026-09-25T09:00:00Z" },
          },
        };
      }
      return { json: { membership_id: 55, status: "DONE", version: 6 } };
    });
    setup();
    const select = screen.getByLabelText("Statut de traitement");

    await userEvent.selectOptions(select, "DONE");

    const message = await screen.findByText(/modifié entre-temps par Bob/);
    expect(message).toHaveTextContent("En attente");
    expect(select).toHaveValue("WAITING"); // replaced by the current server state

    await userEvent.selectOptions(select, "DONE");
    await waitFor(() => expect(screen.getByText("Statut enregistré.")).toBeInTheDocument());
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts[1].body).toEqual({ status: "DONE", expected_version: 5 });
  });
});
