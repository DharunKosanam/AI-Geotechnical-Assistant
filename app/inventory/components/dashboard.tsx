"use client";

/**
 * Dashboard: KPI cards, the server-computed "Needs attention" feed (GET
 * /api/inventory/alerts — alerts are never recomputed client-side, so the
 * assistant and the UI can never disagree; sorted by severity server-side),
 * the session user's own loans, today's PLAXIS seat status, and the recent
 * audit tail. Alert rows and loan rows deep-link to the item drawer via
 * onOpenItem; "Open board" jumps to the PLAXIS page.
 */
import React from "react";

import s from "../inventory.module.css";
import {
  Alert,
  InvDB,
  dayKey,
  fmtDate,
  fmtDateTime,
  isStaleSeat,
  openLoans,
  overdueDays,
  statusKey,
} from "../lib";
import { Empty, Kpi, SeverityDot } from "./ui";

export type DashboardProps = {
  db: InvDB;
  session: { name: string; email: string };
  onOpenItem: (itemId: string) => void;
  onGoTo: (page: "PLAXIS seats" | "Reservations") => void;
};

export default function Dashboard({ db, session, onOpenItem, onGoTo }: DashboardProps) {
  const now = new Date();
  const loans = openLoans(db.tx);
  const overdue = loans.filter((t) => overdueDays(t.expectedReturn, now) > 0);
  const weekAhead = new Date(now.getTime() + 7 * 86_400_000);
  const upcomingRes = db.res.filter((r) => {
    if ((r.status || "").toLowerCase() === "denied") return false;
    const start = r.start ? new Date(r.start) : null;
    return start !== null && start >= new Date(now.getTime() - 86_400_000) && start <= weekAhead;
  });
  const seatsInUse = db.plaxis.filter((p) => !p.loggedOut).length;
  const lowStock = db.items.filter(
    (i) => i.kind === "consumable" && typeof i.minStock === "number" && (i.qty ?? 0) <= i.minStock,
  ).length;
  const availableNow = db.items.filter((i) => statusKey(i) === "available").length;
  const inMaintenance = db.items.filter((i) => statusKey(i) === "maintenance").length;

  // "Checked out to you": the session user's open loans — matched on email
  // first (what the server records), name as the fallback for legacy rows.
  const email = session.email.trim().toLowerCase();
  const name = session.name.trim().toLowerCase();
  const mine = loans.filter(
    (t) =>
      (email && (t.email || "").trim().toLowerCase() === email) ||
      (name && (t.user || "").trim().toLowerCase() === name),
  );

  const todayKey = dayKey(now);
  const todayPx = db.plaxis.filter((b) => dayKey(b.start) === todayKey);
  const itemName = (id?: string | null) => db.items.find((i) => i.id === id)?.name;

  const alertTarget = (a: Alert) => {
    if (a.itemId) return () => onOpenItem(a.itemId as string);
    if (a.kind === "plaxis_overrun") return () => onGoTo("PLAXIS seats");
    return null;
  };

  return (
    <div>
      <div className={s.kpiRow}>
        <Kpi label="Items on record" value={db.items.length} />
        <Kpi label="Available now" value={availableNow} tone="accent" />
        <Kpi label="On loan" value={loans.length} />
        <Kpi label="Overdue" value={overdue.length} tone={overdue.length ? "danger" : undefined} />
        <Kpi label="Low or out of stock" value={lowStock} tone={lowStock ? "warn" : undefined} />
        <Kpi label="In maintenance" value={inMaintenance} tone={inMaintenance ? "warn" : undefined} />
        <Kpi label="Reservations · 7d" value={upcomingRes.length} />
        <Kpi label="PLAXIS seats in use" value={`${seatsInUse} / 2`} tone={seatsInUse >= 2 ? "warn" : undefined} />
      </div>

      <div className={s.twoCol}>
        <div className={s.panel}>
          <div className={s.toolbar}>
            <div className={s.panelTitle} style={{ marginBottom: 0 }}>Needs attention</div>
            <span className={s.spacer} />
            <span className={s.rowNote}>{db.alerts.length} open</span>
          </div>
          {db.alerts.length === 0 ? (
            <Empty>Nothing outstanding — no overdue loans, low stock or service due.</Empty>
          ) : (
            db.alerts.map((a, i) => {
              const go = alertTarget(a);
              const body = (
                <>
                  <SeverityDot severity={a.severity} />
                  <span className={s.alertKind}>{a.kind.replace(/_/g, " ")}</span>
                  <span>{a.detail}</span>
                </>
              );
              return go ? (
                <div
                  key={i}
                  className={s.alertRowLink}
                  role="button"
                  tabIndex={0}
                  onClick={go}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") go();
                  }}
                  title={a.itemId ? `Open ${itemName(a.itemId) || a.itemId}` : "Open the PLAXIS board"}
                >
                  {body}
                </div>
              ) : (
                <div key={i} className={s.alertRow}>{body}</div>
              );
            })
          )}
        </div>

        <div>
          <div className={s.panel}>
            <div className={s.panelTitle}>Checked out to you</div>
            {mine.length === 0 ? (
              <Empty>Nothing on loan — open Inventory to check something out.</Empty>
            ) : (
              mine.map((t) => {
                const it = db.items.find((i) => i.id === t.itemId);
                const late = overdueDays(t.expectedReturn, now) > 0;
                return (
                  <div
                    key={t.id}
                    className={`${s.listRow} ${s.linkRow}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => onOpenItem(t.itemId)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") onOpenItem(t.itemId);
                    }}
                  >
                    <div>
                      <div className={s.cellStrong}>{it?.name || t.itemId}</div>
                      <div className={late ? s.dangerText : s.rowNote}>
                        {t.qty ?? 1} {it?.unit || ""} · due {fmtDate(t.expectedReturn)}{late ? " — overdue" : ""}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <div className={s.panel}>
            <div className={s.toolbar}>
              <div className={s.panelTitle} style={{ marginBottom: 0 }}>PLAXIS today</div>
              <span className={s.spacer} />
              <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => onGoTo("PLAXIS seats")}>
                Open board
              </button>
            </div>
            {todayPx.length === 0 ? (
              <span className={s.muted}>Both seats free today.</span>
            ) : (
              todayPx.map((b) => {
                const stale = isStaleSeat(b, now);
                return (
                  <div key={b.id} className={s.listRow}>
                    <span className={`${s.chip} ${s.chip_reserved}`}>Seat {(b.seat ?? 0) + 1}</span>
                    <div>
                      <div className={s.cellStrong}>{b.user}</div>
                      <div className={s.rowNote}>
                        {fmtDateTime(b.start).slice(11)}–{fmtDateTime(b.end).slice(11)}
                        {b.loggedOut ? " · logged out" : ""}
                      </div>
                    </div>
                    <span className={s.spacer} />
                    {stale && <span className={`${s.chip} ${s.chip_maintenance}`}>Not logged out</span>}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      <div className={s.panel}>
        <div className={s.panelTitle}>Recent activity</div>
        {db.audit.length === 0 ? (
          <Empty>No activity recorded yet.</Empty>
        ) : (
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {db.audit.slice(0, 12).map((a) => (
                  <tr key={a.id}>
                    <td className={s.cellNum}>{fmtDateTime(a.ts)}</td>
                    <td className={s.cellStrong}>{a.actor || "—"}</td>
                    <td>{a.action || "—"}</td>
                    <td className={s.cellNum}>{a.entity || "—"}</td>
                    <td className={s.muted}>
                      {typeof a.detail === "string" ? a.detail : a.detail ? JSON.stringify(a.detail) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
