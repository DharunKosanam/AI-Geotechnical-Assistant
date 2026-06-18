"use client";

/**
 * Client-side auth context.
 *
 * Holds the current user (id/email/name/role) -- NOT the token. The token lives
 * only in the httpOnly cookie; JS never reads it. On mount we ask the backend
 * who we are via getCurrentUser() (GET /auth/me, cookie-authenticated).
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { getCurrentUser, logout as apiLogout, type AuthUser } from "./auth";

type AuthState = {
  user: AuthUser | null;
  loading: boolean; // true while the initial /auth/me check is in flight
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getCurrentUser()
      .then((u) => {
        if (active) setUser(u);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function signOut() {
    try {
      await apiLogout(); // clears the httpOnly cookie on the backend
    } catch {
      // Clear the local user regardless -- the UI must reflect logged-out.
    }
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
