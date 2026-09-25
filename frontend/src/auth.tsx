import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, setUnauthorizedHandler } from "./api/client";
import { keys, useMe } from "./api/hooks";
import type { User } from "./api/types";

interface AuthValue {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const me = useMe();
  const user = me.data ?? null;

  const clear = useCallback(() => {
    client.clear();
    client.setQueryData(keys.me, undefined);
    void navigate("/login", { replace: true });
  }, [client, navigate]);

  // Any 401 from the API means the session ended: return to the login page.
  useEffect(() => {
    setUnauthorizedHandler(clear);
    return () => setUnauthorizedHandler(null);
  }, [clear]);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      loading: me.isLoading,
      isAdmin: user?.role === "ADMIN",
      login: async (username, password) => {
        const signedIn = await api<User>("/auth/login", { method: "POST", json: { username, password } });
        client.setQueryData(keys.me, signedIn);
      },
      logout: async () => {
        try {
          await api("/auth/logout", { method: "POST" });
        } catch (error) {
          if (!(error instanceof ApiError)) throw error;
        }
        clear();
      },
    }),
    [user, me.isLoading, client, clear],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
