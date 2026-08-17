"use client";
import React, { useState } from "react";
import Chat from "./components/chat";
import Header from "./components/Header";
import styles from "./page.module.css";
import { AuthProvider } from "./lib/auth-context";
import AuthGuard from "./components/auth-guard";

const FileSearchPage = () => {
  // Sidebar collapse lives here so the top-bar toggle and the sidebar's own
  // toggle drive the same state.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const toggleSidebar = () => setSidebarCollapsed((c) => !c);

  // Narrow screens start collapsed — below 720px the sidebar overlays the
  // conversation (sidebar.module.css), so defaulting it open would cover it.
  React.useEffect(() => {
    if (window.matchMedia("(max-width: 720px)").matches) {
      setSidebarCollapsed(true);
    }
  }, []);

  return (
    <AuthProvider>
      <AuthGuard>
        <main className={styles.main}>
          <Header sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
          <div className={styles.container}>
            <div className={styles.chatContainer}>
              <div className={styles.chat}>
                <Chat sidebarCollapsed={sidebarCollapsed} onToggleSidebar={toggleSidebar} />
              </div>
            </div>
          </div>
        </main>
      </AuthGuard>
    </AuthProvider>
  );
};

export default FileSearchPage;
