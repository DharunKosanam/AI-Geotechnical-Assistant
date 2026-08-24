"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers, MessageSquare, Compass, Library, Package, PanelLeft } from "lucide-react";
import AccountMenu from "./account-menu";
import styles from "./header.module.css";

/**
 * Shared app top bar (48px, backdrop blur).
 *
 * Left: on the chat page, the sidebar collapse toggle (state lives in
 * page.tsx so the sidebar's own toggle stays in sync); elsewhere, the brand.
 * Centre: the three-mode segmented nav. Right: the account popover.
 *
 * The segmented nav is only shown when the backend features are enabled --
 * the backend is the single source of truth: we ask GET /api/workspace/status
 * and GET /api/kb/status (cookie-authenticated) once on mount.
 */
type HeaderProps = {
  sidebarCollapsed?: boolean;
  onToggleSidebar?: () => void;
};

const Header = ({ sidebarCollapsed, onToggleSidebar }: HeaderProps) => {
  const pathname = usePathname();
  const [workspaceEnabled, setWorkspaceEnabled] = useState(false);
  const [kbEnabled, setKbEnabled] = useState(false);
  const [inventoryEnabled, setInventoryEnabled] = useState(false);

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
    // INVENTORY_ENABLED off = router absent = 404, which ask() reads as
    // disabled — the tab is simply hidden, nothing crashes or hangs.
    ask("/api/inventory/status", setInventoryEnabled);
    return () => {
      active = false;
    };
  }, []);

  const onWorkspace = pathname?.startsWith("/workspace") ?? false;
  const onKb = pathname?.startsWith("/knowledge-base") ?? false;
  const onInventory = pathname?.startsWith("/inventory") ?? false;
  const onChat = !onWorkspace && !onKb && !onInventory;

  return (
    <header className={styles.header}>
      <div className={styles.left}>
        {onToggleSidebar ? (
          <button
            type="button"
            className={styles.iconBtn}
            onClick={onToggleSidebar}
            aria-label={sidebarCollapsed ? "Open sidebar" : "Collapse sidebar"}
            aria-pressed={sidebarCollapsed}
            title={sidebarCollapsed ? "Open sidebar" : "Collapse sidebar"}
          >
            <PanelLeft size={16} strokeWidth={1.5} />
          </button>
        ) : (
          <div className={styles.brand}>
            <Layers size={16} strokeWidth={1.5} />
            <span className={styles.appName}>GeoTech AI</span>
          </div>
        )}
      </div>

      {(workspaceEnabled || kbEnabled || inventoryEnabled) && (
        <nav className={styles.toggle} aria-label="Section switch">
          <Link
            href="/"
            className={`${styles.segment} ${onChat ? styles.segmentActive : ""}`}
            aria-current={onChat ? "page" : undefined}
          >
            <MessageSquare size={14} strokeWidth={1.5} />
            <span className={styles.segmentLabel}>Chat</span>
          </Link>
          {workspaceEnabled && (
            <Link
              href="/workspace"
              className={`${styles.segment} ${onWorkspace ? styles.segmentActive : ""}`}
              aria-current={onWorkspace ? "page" : undefined}
            >
              <Compass size={14} strokeWidth={1.5} />
              <span className={styles.segmentLabel}>GeoPilot</span>
            </Link>
          )}
          {kbEnabled && (
            <Link
              href="/knowledge-base"
              className={`${styles.segment} ${onKb ? styles.segmentActive : ""}`}
              aria-current={onKb ? "page" : undefined}
            >
              <Library size={14} strokeWidth={1.5} />
              <span className={styles.segmentLabel}>Knowledge Base</span>
            </Link>
          )}
          {inventoryEnabled && (
            <Link
              href="/inventory"
              className={`${styles.segment} ${onInventory ? styles.segmentActive : ""}`}
              aria-current={onInventory ? "page" : undefined}
            >
              <Package size={14} strokeWidth={1.5} />
              <span className={styles.segmentLabel}>Inventory</span>
            </Link>
          )}
        </nav>
      )}

      <div className={styles.right}>
        <AccountMenu />
      </div>
    </header>
  );
};

export default Header;
