"use client";

import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";
import type { AuthAdapter, SignedInUser } from "./types";

export type AuthStatus = "loading" | "signed-in" | "signed-out";

export interface AuthState {
  status: AuthStatus;
  user: SignedInUser | null;
  adapter: AuthAdapter;
}

const AuthContext = createContext<AuthState | null>(null);

export interface AuthProviderProps {
  adapter: AuthAdapter;
  children: ReactNode;
}

export function AuthProvider({ adapter, children }: AuthProviderProps) {
  const [user, setUser] = useState<SignedInUser | null | undefined>(undefined);

  useEffect(() => {
    setUser(undefined);
    return adapter.subscribe(setUser);
  }, [adapter]);

  const value = useMemo<AuthState>(
    () => ({
      adapter,
      user: user ?? null,
      status: user === undefined ? "loading" : user ? "signed-in" : "signed-out",
    }),
    [adapter, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const state = useContext(AuthContext);
  if (!state) throw new Error("useAuth must be used inside <AuthProvider>");
  return state;
}
