import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, setUnauthorizedHandler } from "./client";
import { buildItemsQuery } from "./hooks";

function respond(status: number, json?: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(status === 204 ? null : JSON.stringify(json ?? {}), { status })),
  );
}

afterEach(() => {
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("returns parsed JSON and sends same-origin credentials", async () => {
    respond(200, { ok: true });
    await expect(api("/health")).resolves.toEqual({ ok: true });
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/health");
    expect(init.credentials).toBe("same-origin");
  });

  it("sends a 401 to the unauthorized handler so the app returns to the login page", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    respond(401, { detail: "Authentification requise." });

    await expect(api("/dashboard")).rejects.toMatchObject({ status: 401 });

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not treat a failed login or the initial /auth/me probe as an expired session", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    respond(401, { detail: "Identifiants invalides ou compte désactivé." });

    await expect(api("/auth/login", { method: "POST", json: {} })).rejects.toThrow(
      "Identifiants invalides ou compte désactivé.",
    );
    await expect(api("/auth/me")).rejects.toBeInstanceOf(ApiError);

    expect(handler).not.toHaveBeenCalled();
  });

  it("surfaces the API's French detail, or a generic message for validation errors", async () => {
    respond(409, { detail: "Cet identifiant existe déjà." });
    await expect(api("/admin/users", { method: "POST", json: {} })).rejects.toThrow("Cet identifiant existe déjà.");
    respond(422, { detail: [{ msg: "x" }] });
    await expect(api("/x")).rejects.toThrow("Certaines valeurs saisies sont invalides.");
  });

  it("turns a network failure into a readable error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed"))));
    await expect(api("/x")).rejects.toMatchObject({ status: 0 });
  });

  it("handles 204 without a body", async () => {
    respond(204);
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeNull();
  });
});

describe("buildItemsQuery", () => {
  it("drops empty values and repeats multi-value keys", () => {
    expect(buildItemsQuery({ search: "karim", unread: "", workflow: ["a", "b"], page: undefined })).toBe(
      "search=karim&workflow=a&workflow=b",
    );
  });

  it("accepts URLSearchParams", () => {
    expect(buildItemsQuery(new URLSearchParams("unread=true&kind=arrival&empty="))).toBe("unread=true&kind=arrival");
  });
});
