import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { vi } from "vitest";
import type { ItemView, MembershipView, User, WorkflowView } from "../api/types";
import { AuthProvider } from "../auth";
import { ToastProvider } from "../components/ui/Toast";
import { ThemeProvider } from "../theme";

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Reply = { status?: number; json?: unknown } | undefined;

/** Replaces window.fetch with a router over `/api/v1` paths; records every call. */
export function installFetch(handler: (call: Call) => Reply) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const call: Call = {
        method: init?.method ?? "GET",
        path: url.replace("/api/v1", ""),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };
      calls.push(call);
      const reply = handler(call) ?? { status: 404, json: { detail: "inconnu" } };
      const status = reply.status ?? 200;
      return new Response(status === 204 ? null : JSON.stringify(reply.json ?? {}), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

export function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

export function renderApp(ui: ReactElement, route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={[route]}>
            <AuthProvider>
              {ui}
              <LocationProbe />
            </AuthProvider>
          </MemoryRouter>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

export const employee: User = { id: 2, username: "alice", display_name: "Alice", role: "EMPLOYEE" };
export const admin: User = { id: 1, username: "admin", display_name: "Admin", role: "ADMIN" };

export function makeItem(overrides: Partial<ItemView> = {}): ItemView {
  return {
    active: true,
    city: "",
    detected_at: "2026-09-25T08:00:00Z",
    dossier_id: 10,
    dossier_number: "D-100",
    fields: {},
    garage: "Garage Test",
    insured_name: "Sara Test",
    membership_id: 55,
    notification_class: "ACTION",
    occurrence_id: 77,
    occurrence_number: 1,
    occurrence_origin: "NEW",
    omegaflow_url: "https://omegaflow.example/#q/view-dossier-details/x/",
    portal_status: "En cours",
    primary_date: null,
    primary_date_label: null,
    primary_date_raw: "",
    procedure: "Garage agréé",
    queue_age_days: 2,
    record_id: "rec1",
    registration: "1-A-1",
    rules_status: "CAPTURE_DERIVED",
    unread: true,
    unread_kind: "WORKFLOW_ITEM_NEW",
    work_status: "TO_DO",
    work_version: 1,
    workflow_category: "Devis et photos",
    workflow_key: "photos_pending",
    workflow_name: "Dossiers en instance Photos",
    ...overrides,
  };
}

export function makePage(items: ItemView[], extra: Record<string, unknown> = {}) {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 25,
    facets: { portal_statuses: ["En cours"], procedures: ["Garage agréé"], workflows: [] },
    columns: [],
    ...extra,
  };
}

export function makeWorkflow(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    key: "photos_pending",
    name: "Dossiers en instance Photos",
    category: "Devis et photos",
    route: "#dossiers-en-instance-photos/",
    view_id: "view_787",
    notification_class: "ACTION",
    rules_status: "CAPTURE_DERIVED",
    needs_site_validation: true,
    enabled: true,
    baseline_completed_at: null,
    last_poll_at: null,
    last_success_at: null,
    last_poll_status: null,
    last_error: null,
    active_count: 3,
    unread_new: 1,
    unread_changed: 0,
    by_work_status: {},
    last_run: null,
    ...overrides,
  } as WorkflowView;
}

export function makeMembership(overrides: Partial<MembershipView> = {}): MembershipView {
  return {
    membership_id: 55,
    workflow_key: "photos_pending",
    workflow_name: "Dossiers en instance Photos",
    workflow_category: "Devis et photos",
    notification_class: "ACTION",
    rules_status: "CAPTURE_DERIVED",
    active: true,
    first_seen_at: "2026-09-25T08:00:00Z",
    last_seen_at: "2026-09-25T09:00:00Z",
    work_status: "TO_DO",
    work_version: 1,
    work_updated_at: null,
    work_updated_by: null,
    current_occurrence_id: 77,
    occurrences: [{ id: 77, number: 1, origin: "NEW", detected_at: "2026-09-25T08:00:00Z", unread: true }],
    fields: [],
    primary_date: null,
    primary_date_raw: "",
    omegaflow_url: "https://omegaflow.example/#q/view-dossier-details/x/",
    ...overrides,
  } as MembershipView;
}
