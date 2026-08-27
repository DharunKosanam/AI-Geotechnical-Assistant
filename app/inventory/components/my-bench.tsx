"use client";

/**
 * My Bench (INVENTORY_PERSONAL_VIEW): the signed-in user's own slice —
 * open loans, upcoming reservations, held PLAXIS seats, and the alerts that
 * name them. Everything renders from GET /api/inventory/me (server-filtered
 * over the same full fetch every other read uses; overdue days come from the
 * SERVER clock). Rows deep-link into the item drawer like the Dashboard's.
 * The page only exists when the flag is on — flag off, the tab is absent and
 * nothing here mounts.
 */
import React from "react";

import s from "../inventory.module.css";
import { InvDB, MyBench as MyBenchData, fmtDate, fmtDateTime } from "../lib";
import { Empty, SeverityDot } from "./ui";

export default function MyBenchPage({
  me,
  db,
  onOpenItem,
  onGoTo,
}: {
  me: MyBenchData;
  db: InvDB;
  onOpenItem: (itemId: string) => void;
  onGoTo: (page: "PLAXIS seats" | "Reservations") => void;
}) {
  const itemName = (id?: string | null) => db.items.find((i) => i.id === id)?.name || id || "—";
  const resChip = (status?: string) => {
    const v = (status || "").toLowerCase();
    if (v === "approved") return s.chip_available;
    if (v === "denied") return s.chip_retired;
    return s.chip_reserved; // Pending
  };

  return (
    <div>
      <div className={s.panel}>
        <div className={s.panelTitle}>Checked out to you</div>
        {me.loans.length === 0 ? (
          <Empty>No items currently checked out to you.</Empty>
        ) : (
          me.loans.map((t) => {
            const late = (t.overdueDays ?? 0) > 0;
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
                  <div className={s.cellStrong}>{itemName(t.itemId)}</div>
                  <div className={late ? s.dangerText : s.rowNote}>
                    {t.qty ?? 1} out since {fmtDate(t.ts)} · due {fmtDate(t.expectedReturn)}
                    {late ? ` — ${t.overdueDays}d overdue` : ""}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className={s.twoCol}>
        <div className={s.panel}>
          <div className={s.toolbar}>
            <div className={s.panelTitle} style={{ marginBottom: 0 }}>Your reservations</div>
            <span className={s.spacer} />
            <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => onGoTo("Reservations")}>
              All reservations
            </button>
          </div>
          {me.reservations.length === 0 ? (
            <Empty>No upcoming reservations — nothing is booked under your name.</Empty>
          ) : (
            me.reservations.map((r) => (
              <div
                key={r.id}
                className={`${s.listRow} ${s.linkRow}`}
                role="button"
                tabIndex={0}
                onClick={() => onOpenItem(r.itemId)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onOpenItem(r.itemId);
                }}
              >
                <div>
                  <div className={s.cellStrong}>{itemName(r.itemId)}</div>
                  <div className={s.rowNote}>
                    {fmtDateTime(r.start)} → {fmtDateTime(r.end)}
                    {r.purpose ? ` · ${r.purpose}` : ""}
                  </div>
                </div>
                <span className={s.spacer} />
                <span className={`${s.chip} ${resChip(r.status)}`}>{r.status || "Pending"}</span>
              </div>
            ))
          )}
        </div>

        <div className={s.panel}>
          <div className={s.toolbar}>
            <div className={s.panelTitle} style={{ marginBottom: 0 }}>Your PLAXIS seats</div>
            <span className={s.spacer} />
            <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => onGoTo("PLAXIS seats")}>
              Open board
            </button>
          </div>
          {me.plaxis.length === 0 ? (
            <Empty>You are not holding a PLAXIS seat.</Empty>
          ) : (
            me.plaxis.map((p) => {
              const stale = !p.loggedOut && p.end ? new Date(p.end) < new Date() : false;
              return (
                <div key={p.id} className={s.listRow}>
                  <span className={`${s.chip} ${s.chip_reserved}`}>Seat {(p.seat ?? 0) + 1}</span>
                  <div>
                    <div className={s.rowNote}>
                      {fmtDateTime(p.start)} → {fmtDateTime(p.end)}
                      {p.purpose ? ` · ${p.purpose}` : ""}
                    </div>
                  </div>
                  <span className={s.spacer} />
                  {stale && <span className={`${s.chip} ${s.chip_maintenance}`}>Held past end</span>}
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className={s.panel}>
        <div className={s.panelTitle}>Alerts for you</div>
        {me.alerts.length === 0 ? (
          <Empty>Nothing here needs your attention.</Empty>
        ) : (
          me.alerts.map((a, i) => {
            const go = a.itemId ? () => onOpenItem(a.itemId as string) : null;
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
              >
                {body}
              </div>
            ) : (
              <div key={i} className={s.alertRow}>{body}</div>
            );
          })
        )}
      </div>
    </div>
  );
}
