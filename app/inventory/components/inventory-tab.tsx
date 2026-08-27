"use client";

/**
 * Inventory tab shell: page head + the six-page tablist (with the
 * high-alert / pending-reservation badges) + the active sub-page. Every
 * sub-page renders INSIDE this shell — none draws its own header — so the
 * nav persists across pages. Kept out of page.tsx so it can be rendered in
 * tests (Next.js rejects extra exports from a page file).
 */
import React, { useCallback, useMemo, useState } from "react";

import { useAuth } from "../../lib/auth-context";

import { makeActions } from "../actions";
import s from "../inventory.module.css";
import { callerIdentity, roleFlags, sessionPrefill } from "../lib";
import { useInventory } from "../use-inventory";
import Dashboard from "./dashboard";
import ItemsPage from "./items";
import MyBenchPage from "./my-bench";
import PeoplePage from "./people";
import PlaxisPage from "./plaxis";
import ReportsPage from "./reports";
import ReservationsPage from "./reservations";
import { Eyebrow } from "./ui";

export const SUBPAGES = [
  "Dashboard",
  "Inventory",
  "Reservations",
  "PLAXIS seats",
  "Reports",
  "People & log",
] as const;

/** First in the tab order and the default landing page — but ONLY when the
 * personal view is enabled (flag off: the six pages above, exactly as today). */
export const PERSONAL_SUBPAGE = "My Bench" as const;

type Subpage = (typeof SUBPAGES)[number];
type PageName = Subpage | typeof PERSONAL_SUBPAGE;

export function InventoryTab() {
  const { user } = useAuth();
  const { db, state, loadError, load, mutate, photosEnabled, personal } = useInventory();
  const personalView = Boolean(personal?.enabled);
  // null = "the default landing page", which depends on the flag (known only
  // after the load): My Bench when the personal view is on, else Dashboard.
  const [chosenPage, setChosenPage] = useState<PageName | null>(null);
  const subpage: PageName = chosenPage ?? (personalView ? PERSONAL_SUBPAGE : "Dashboard");
  const setSubpage = setChosenPage;
  const pages: readonly PageName[] = personalView ? [PERSONAL_SUBPAGE, ...SUBPAGES] : SUBPAGES;
  // Item-drawer selection lives here so any page can deep-link into it.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const openItem = useCallback((id: string) => {
    setSelectedId(id);
    setChosenPage("Inventory");
  }, []);

  const actions = useMemo(() => makeActions(mutate), [mutate]);
  // isManager / isPI derive from the authenticated JWT role — presentation
  // only; the server-side auth is the real gate.
  const { isManager } = roleFlags(user?.role);
  const prefill = useMemo(() => sessionPrefill(user, db.users), [user, db.users]);
  // Ownership identity for control gating + "Mine only" (client mirror of
  // the server rule; the server 403s regardless of what renders).
  const identity = useMemo(() => callerIdentity(user, db.users), [user, db.users]);
  // Nav badges (the prototype's rail counts): high-severity alerts on the
  // Dashboard, pending reservations on Reservations — both from server data.
  const badges: Partial<Record<PageName, { count: number; warn?: boolean }>> = {
    Dashboard: { count: db.alerts.filter((a) => a.severity === "high").length },
    Reservations: {
      count: db.res.filter((r) => (r.status || "").toLowerCase() === "pending").length,
      warn: true,
    },
  };

  if (state === "disabled") {
    return (
      <div className={s.statePanel}>
        <Eyebrow>Inventory</Eyebrow>
        <p>Lab inventory is not enabled on this deployment.</p>
      </div>
    );
  }

  if (state === "loading") {
    return (
      <div className={s.statePanel} aria-busy="true">
        <Eyebrow>Inventory</Eyebrow>
        <p>Loading the lab inventory…</p>
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className={s.statePanel} role="alert">
        <Eyebrow>Inventory</Eyebrow>
        <p>{loadError || "The inventory could not be loaded."}</p>
        <button type="button" className={s.btnPrimary} onClick={() => void load()}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className={s.wrap}>
      <div className={s.pageHead}>
        <Eyebrow>Lin Lab</Eyebrow>
        <h1 className={s.heading}>Inventory</h1>
      </div>

      <nav className={s.subnav} role="tablist" aria-label="Inventory pages">
        {pages.map((p) => {
          const badge = badges[p];
          return (
            <button
              key={p}
              type="button"
              role="tab"
              aria-selected={subpage === p}
              className={`${s.subTab} ${subpage === p ? s.subTabActive : ""}`}
              onClick={() => setSubpage(p)}
            >
              {p}
              {badge && badge.count > 0 && (
                <span className={badge.warn ? s.subTabCount_warn : s.subTabCount}>{badge.count}</span>
              )}
            </button>
          );
        })}
      </nav>

      {subpage === PERSONAL_SUBPAGE && personal?.me && (
        <MyBenchPage me={personal.me} db={db} onOpenItem={openItem} onGoTo={setSubpage} />
      )}
      {subpage === "Dashboard" && (
        <Dashboard
          db={db}
          session={{ name: prefill.name, email: prefill.email }}
          onOpenItem={openItem}
          onGoTo={setSubpage}
        />
      )}
      {subpage === "Inventory" && (
        <ItemsPage
          db={db}
          actions={actions}
          prefill={prefill}
          isManager={isManager}
          selectedId={selectedId}
          onSelect={setSelectedId}
          photosEnabled={photosEnabled}
          personalView={personalView}
          identity={identity}
        />
      )}
      {subpage === "Reservations" && (
        <ReservationsPage
          db={db}
          actions={actions}
          prefill={prefill}
          isManager={isManager}
          onOpenItem={openItem}
          personalView={personalView}
          identity={identity}
        />
      )}
      {subpage === "PLAXIS seats" && (
        <PlaxisPage
          db={db}
          actions={actions}
          prefill={prefill}
          isManager={isManager}
          personalView={personalView}
          identity={identity}
        />
      )}
      {subpage === "Reports" && <ReportsPage db={db} onOpenItem={openItem} />}
      {subpage === "People & log" && (
        <PeoplePage db={db} actions={actions} isManager={isManager} onReload={load} mutate={mutate} photosEnabled={photosEnabled} personalView={personalView} />
      )}
    </div>
  );
}
