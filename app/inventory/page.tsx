"use client";

/**
 * Inventory — the fourth top-level tab (Chat / GeoPilot / Knowledge Base /
 * Inventory). Same shell as the other tabs: AuthProvider + AuthGuard +
 * shared Header; the tab body (head, six-page tablist, active page) lives in
 * components/inventory-tab.tsx so it can be tested.
 */
import React from "react";

import AuthGuard from "../components/auth-guard";
import Header from "../components/Header";
import { AuthProvider } from "../lib/auth-context";
import pageStyles from "../page.module.css";

import { InventoryTab } from "./components/inventory-tab";

const InventoryRoute = () => (
  <AuthProvider>
    <AuthGuard>
      <main className={pageStyles.main}>
        <Header />
        <InventoryTab />
      </main>
    </AuthGuard>
  </AuthProvider>
);

export default InventoryRoute;
