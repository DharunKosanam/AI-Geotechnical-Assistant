"use client";

/**
 * Sidebar account block: shows who is logged in and a logout control.
 * Self-contained (uses the auth context directly) so chat.tsx only needs to
 * drop it into the sidebar.
 */
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";

import { useAuth } from "../lib/auth-context";
import styles from "./sidebar-account.module.css";

export default function SidebarAccount() {
  const { user, signOut } = useAuth();
  const router = useRouter();

  async function handleLogout() {
    await signOut(); // clears the cookie + local user
    router.replace("/login");
  }

  if (!user) return null;

  const displayName = user.full_name?.trim() || user.email;

  return (
    <div className={styles.account}>
      <div className={styles.identity}>
        <span className={styles.name} title={user.email}>
          {displayName}
        </span>
        {user.full_name?.trim() && (
          <span className={styles.email}>{user.email}</span>
        )}
      </div>
      <button
        type="button"
        className={styles.logoutBtn}
        onClick={handleLogout}
        title="Log out"
      >
        <LogOut size={15} />
        Log out
      </button>
    </div>
  );
}
