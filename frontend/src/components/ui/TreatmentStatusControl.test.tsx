import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installFetch } from "../../test/utils";
import { ToastProvider } from "./Toast";
import { TreatmentStatusControl } from "./TreatmentStatusControl";

function setup(variant: "segmented" | "select" = "select") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <TreatmentStatusControl membershipId={55} status="TO_DO" version={3} variant={variant} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("TreatmentStatusControl", () => {
  it("sends the version with the change (optimistic locking), shows it at once and confirms with a toast", async () => {
    const calls = installFetch(() => ({ json: { membership_id: 55, status: "DONE", version: 4 } }));
    setup();

    await userEvent.selectOptions(screen.getByLabelText("Statut de traitement"), "DONE");

    expect(screen.getByLabelText("Statut de traitement")).toHaveValue("DONE");
    expect(await screen.findByText(/Statut passé à « Terminé »/)).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/memberships/55/work-status");
    expect(put?.body).toEqual({ status: "DONE", expected_version: 3 });
  });

  it("undoes a change from the toast, using the version returned by the first save", async () => {
    let call = 0;
    const calls = installFetch(() => {
      call += 1;
      return { json: { membership_id: 55, status: call === 1 ? "DONE" : "TO_DO", version: 3 + call } };
    });
    setup();

    await userEvent.selectOptions(screen.getByLabelText("Statut de traitement"), "DONE");
    await userEvent.click(await screen.findByRole("button", { name: "Annuler" }));

    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PUT")[1].body).toEqual({ status: "TO_DO", expected_version: 4 });
    await waitFor(() => expect(screen.getByLabelText("Statut de traitement")).toHaveValue("TO_DO"));
  });

  it("rolls back to the previous value and reports the error when saving fails", async () => {
    installFetch(() => ({ status: 500, json: { detail: "Base indisponible." } }));
    setup();

    await userEvent.selectOptions(screen.getByLabelText("Statut de traitement"), "WAITING");

    expect(await screen.findByText("Base indisponible.")).toBeInTheDocument();
    expect(screen.getByLabelText("Statut de traitement")).toHaveValue("TO_DO");
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
    expect(select).toHaveValue("WAITING");

    await userEvent.selectOptions(select, "DONE");
    await screen.findByText(/Statut passé à « Terminé »/);
    expect(calls.filter((c) => c.method === "PUT")[1].body).toEqual({ status: "DONE", expected_version: 5 });
  });

  it("is a radio group on the dossier page and moves with the arrow keys", async () => {
    const calls = installFetch(() => ({ json: { membership_id: 55, status: "IN_PROGRESS", version: 4 } }));
    setup("segmented");

    expect(screen.getByRole("radiogroup", { name: "Statut de traitement" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "À traiter" })).toBeChecked();
    screen.getByRole("radio", { name: "À traiter" }).focus();
    await userEvent.keyboard("{ArrowRight}");

    await waitFor(() => expect(screen.getByRole("radio", { name: "En cours" })).toBeChecked());
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ status: "IN_PROGRESS", expected_version: 3 });
  });
});
