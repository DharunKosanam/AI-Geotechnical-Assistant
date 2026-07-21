"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers, MessageSquare, Compass, User } from "lucide-react";
import styles from "./header.module.css";

/**
 * Shared app header.
 *
 * Adds a Chat / GeoPilot segmented toggle (like Claude's chat/code switch) that
 * routes between the existing chat ("/") and the GeoPilot workspace
 * ("/workspace"). The toggle is only shown when the workspace feature is
 * enabled -- the backend is the single source of truth: we ask
 * GET /api/workspace/status (cookie-authenticated) once on mount. When the flag
 * is off the toggle never renders and the header looks exactly as before.
 */
const Header = () => {
  const pathname = usePathname();
  const [workspaceEnabled, setWorkspaceEnabled] = useState(false);

  useEffect(() => {
    let active = true;
    fetch("/api/workspace/status", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { enabled: false }))
      .then((d) => {
        if (active) setWorkspaceEnabled(Boolean(d?.enabled));
      })
      .catch(() => {
        if (active) setWorkspaceEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const onWorkspace = pathname?.startsWith("/workspace") ?? false;

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <Layers size={20} />
        <span className={styles.appName}>GeoTech AI</span>
      </div>

      {workspaceEnabled && (
        <nav className={styles.toggle} aria-label="Workspace switch">
          <Link
            href="/"
            className={`${styles.segment} ${!onWorkspace ? styles.segmentActive : ""}`}
            aria-current={!onWorkspace ? "page" : undefined}
          >
            <MessageSquare size={15} />
            <span>Chat</span>
          </Link>
          <Link
            href="/workspace"
            className={`${styles.segment} ${onWorkspace ? styles.segmentActive : ""}`}
            aria-current={onWorkspace ? "page" : undefined}
          >
            <Compass size={15} />
            <span>GeoPilot</span>
          </Link>
        </nav>
      )}

      {/* Placeholder for future auth — intentionally a no-op. */}
      <button type="button" className={styles.avatar} aria-label="Account">
        <User size={16} />
      </button>
    </header>
  );
};

export default Header;
