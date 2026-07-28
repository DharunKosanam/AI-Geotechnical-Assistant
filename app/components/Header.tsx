"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers, MessageSquare, Compass, User, Library } from "lucide-react";
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
  const [kbEnabled, setKbEnabled] = useState(false);

  useEffect(() => {
    let active = true;
    const ask = (url: string, set: (v: boolean) => void) =>
      fetch(url, { credentials: "include" })
        .then((r) => (r.ok ? r.json() : { enabled: false }))
        .then((d) => {
          if (active) set(Boolean(d?.enabled));
        })
        .catch(() => {
          if (active) set(false);
        });
    ask("/api/workspace/status", setWorkspaceEnabled);
    ask("/api/kb/status", setKbEnabled);
    return () => {
      active = false;
    };
  }, []);

  const onWorkspace = pathname?.startsWith("/workspace") ?? false;
  const onKb = pathname?.startsWith("/knowledge-base") ?? false;
  const onChat = !onWorkspace && !onKb;

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <Layers size={20} />
        <span className={styles.appName}>GeoTech AI</span>
      </div>

      {(workspaceEnabled || kbEnabled) && (
        <nav className={styles.toggle} aria-label="Section switch">
          <Link
            href="/"
            className={`${styles.segment} ${onChat ? styles.segmentActive : ""}`}
            aria-current={onChat ? "page" : undefined}
          >
            <MessageSquare size={15} />
            <span>Chat</span>
          </Link>
          {workspaceEnabled && (
            <Link
              href="/workspace"
              className={`${styles.segment} ${onWorkspace ? styles.segmentActive : ""}`}
              aria-current={onWorkspace ? "page" : undefined}
            >
              <Compass size={15} />
              <span>GeoPilot</span>
            </Link>
          )}
          {kbEnabled && (
            <Link
              href="/knowledge-base"
              className={`${styles.segment} ${onKb ? styles.segmentActive : ""}`}
              aria-current={onKb ? "page" : undefined}
            >
              <Library size={15} />
              <span>Knowledge Base</span>
            </Link>
          )}
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
