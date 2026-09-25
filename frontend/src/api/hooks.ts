import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AdminUser,
  AiOutcome,
  AiStatus,
  Dashboard,
  DossierHit,
  DossierView,
  ItemPage,
  NoteView,
  NotificationClass,
  RulesStatus,
  SyncHealth,
  User,
  WorkState,
  WorkStatus,
  WorkflowDetail,
  WorkflowView,
} from "./types";

export const keys = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  workflows: ["workflows"] as const,
  workflow: (key: string) => ["workflow", key] as const,
  items: (scope: string, query: string) => ["items", scope, query] as const,
  dossier: (id: number) => ["dossier", id] as const,
  sync: ["sync"] as const,
  ai: ["ai-status"] as const,
  users: ["admin-users"] as const,
  adminWorkflows: ["admin-workflows"] as const,
};

/** Serialize filters to the API query string, dropping empty values (repeated keys allowed). */
export function buildItemsQuery(
  params: URLSearchParams | Record<string, string | string[] | undefined>,
): string {
  const out = new URLSearchParams();
  const entries = params instanceof URLSearchParams ? [...params.entries()] : Object.entries(params);
  for (const [key, value] of entries) {
    if (Array.isArray(value)) value.forEach((v) => v && out.append(key, v));
    else if (value) out.append(key, value);
  }
  return out.toString();
}

export const useMe = () =>
  useQuery({ queryKey: keys.me, queryFn: () => api<User>("/auth/me"), retry: false, staleTime: 60_000 });

export const useDashboard = () =>
  useQuery({ queryKey: keys.dashboard, queryFn: () => api<Dashboard>("/dashboard"), refetchInterval: 30_000 });

export const useWorkflows = () =>
  useQuery({
    queryKey: keys.workflows,
    queryFn: () => api<WorkflowView[]>("/workflows"),
    refetchInterval: 30_000,
  });

export const useWorkflowDetail = (key: string) =>
  useQuery({ queryKey: keys.workflow(key), queryFn: () => api<WorkflowDetail>(`/workflows/${key}`) });

export function useItems(workflowKey: string | null, query: string) {
  const path = workflowKey ? `/workflows/${workflowKey}/items` : "/inbox";
  return useQuery({
    queryKey: keys.items(workflowKey ?? "all", query),
    queryFn: () => api<ItemPage>(`${path}${query ? `?${query}` : ""}`),
    placeholderData: (previous) => previous,
  });
}

export const useDossier = (id: number) =>
  useQuery({ queryKey: keys.dossier(id), queryFn: () => api<DossierView>(`/dossiers/${id}`) });

export const useDossierSearch = (q: string) =>
  useQuery({
    queryKey: ["search", q],
    queryFn: () => api<DossierHit[]>(`/dossiers/search?q=${encodeURIComponent(q)}`),
    enabled: q.trim().length >= 2,
  });

export const useSyncHealth = () =>
  useQuery({ queryKey: keys.sync, queryFn: () => api<SyncHealth>("/sync"), refetchInterval: 15_000 });

/** Opening an occurrence marks it read for the acting employee only. */
export function useAcknowledge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (occurrenceId: number) =>
      api<{ acknowledged: number }>(`/occurrences/${occurrenceId}/acknowledge`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.dashboard });
      void client.invalidateQueries({ queryKey: keys.workflows });
      void client.invalidateQueries({ queryKey: ["items"] });
      void client.invalidateQueries({ queryKey: ["dossier"] });
    },
  });
}

export function useSetWorkStatus() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (v: { membershipId: number; status: WorkStatus; expectedVersion: number }) =>
      api<WorkState>(`/memberships/${v.membershipId}/work-status`, {
        method: "PUT",
        json: { status: v.status, expected_version: v.expectedVersion },
      }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["items"] });
      void client.invalidateQueries({ queryKey: ["dossier"] });
      void client.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export function useAddNote(dossierId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: string; membershipId: number | null }) =>
      api<NoteView>(`/dossiers/${dossierId}/notes`, {
        method: "POST",
        json: { body: v.body, membership_id: v.membershipId },
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.dossier(dossierId) }),
  });
}

export function useRequestSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: boolean }>("/sync/run", { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.sync }),
  });
}

export const useAdminWorkflows = () =>
  useQuery({ queryKey: keys.adminWorkflows, queryFn: () => api<WorkflowView[]>("/admin/workflows") });

export function useUpdateWorkflow() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      key: string;
      enabled?: boolean;
      rules_status?: RulesStatus;
      notification_class?: NotificationClass;
    }) => {
      const { key, ...body } = v;
      return api<WorkflowView>(`/admin/workflows/${key}`, { method: "PATCH", json: body });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.adminWorkflows });
      void client.invalidateQueries({ queryKey: keys.workflows });
      void client.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

export const useUsers = () =>
  useQuery({ queryKey: keys.users, queryFn: () => api<AdminUser[]>("/admin/users") });

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (v: { username: string; display_name: string; password: string; role: string }) =>
      api<AdminUser>("/admin/users", { method: "POST", json: v }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  });
}

export function useSetUserActive() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: number; active: boolean }) =>
      api<AdminUser>(`/admin/users/${v.id}`, { method: "PATCH", json: { active: v.active } }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  });
}

export const useAiStatus = () =>
  useQuery({ queryKey: keys.ai, queryFn: () => api<AiStatus>("/ai/status"), staleTime: 60_000, retry: false });

/** Runs one advisory request; failures of the assistant are returned as data, never thrown. */
export const useAiRun = () =>
  useMutation({ mutationFn: (path: string) => api<AiOutcome>(path, { method: "POST" }) });
