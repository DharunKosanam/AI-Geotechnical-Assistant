"use client";

/**
 * Route guard for authenticated pages.
 *
 * Client-component guard (not middleware): the auth cookie is httpOnly and the
 * check is a quick /auth/me call, so guarding in the client keeps the backend
 * the single source of truth and avoids duplicating token logic at the edge.
 *
 * While the initial check runs we render ONLY a minimal gate (no chat, no
 * login) so there is never a flash of protected content or a redirect blink.
 */
import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "../lib/auth-context";
import styles from "./auth-guard.module.css";

export default function AuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className={styles.gate} role="status" aria-live="polite">
        <span className={styles.spinner} aria-hidden="true" />
        <span className={styles.gateText}>Checking your session...</span>
      </div>
    );
  }

  return <>{children}</>;
}
