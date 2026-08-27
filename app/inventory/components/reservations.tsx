"use client";

/**
 * Reservations page: upcoming + recent requests, manager approve/deny
 * (PUT with expectedUpdatedAt — a concurrent decision 409s into the conflict
 * toast, never a silent overwrite), cancel, and a new-request flow.
 */
import React, { useMemo, useState } from "react";

import { InventoryActions } from "../actions";
import s from "../inventory.module.css";
import {
  CallerIdentity, InvDB, InvItem, InvRes, SessionPrefill, activeItems, asDate, conflictLine,
  fmtDateTime, ownsRowByKey, reservationCalendar, reservationConflicts,
  reservationsEmptyMessage, visibleReservations,
} from "../lib";
import { ReserveModal } from "./modals";
import { Empty } from "./ui";

export default function ReservationsPage({
  db,
  actions,
  prefill,
  isManager,
  onOpenItem,
  personalView = false,
  identity = { email: "", names: [] },
}: {
  db: InvDB;
  actions: InventoryActions;
  prefill: SessionPrefill;
  isManager: boolean;
  onOpenItem: (itemId: string) => void;
  /** INVENTORY_PERSONAL_VIEW: owner-only Cancel + the "Mine only" filter.
   * Off (the default): everything renders exactly as before. */
  personalView?: boolean;
  identity?: CallerIdentity;
}) {
  // "" = All items: the list shows every reservation unless an item is picked.
  const [reserveItemId, setReserveItemId] = useState("");
  // "Mine only": client-side over the already-fetched full list (never a
  // server filter — the full payload is what answers "who has it"). Default
  // ON when the flag is on; the control does not render flag-off.
  const [mineOnly, setMineOnly] = useState(personalView);
  const [modalItem, setModalItem] = useState<InvItem | null>(null);
  // List stays the default; the calendar is the schedule view of the same data.
  const [view, setView] = useState<"list" | "calendar">("list");
  const [anchor, setAnchor] = useState(() => new Date());
  const calendar = useMemo(() => reservationCalendar(db.items, db.res, anchor), [db.items, db.res, anchor]);
  const shiftWeek = (delta: number) => setAnchor((a) => new Date(a.getTime() + delta * 7 * 86_400_000));
  const today = new Date().toDateString();

  const itemName = (id: string) => db.items.find((i) => i.id === id)?.name || id;

  const rows = useMemo(() => {
    const base = visibleReservations(db.res, reserveItemId);
    return personalView && mineOnly ? base.filter((r) => ownsRowByKey(r, identity)) : base;
  }, [db.res, reserveItemId, personalView, mineOnly, identity]);
  // Approve/deny queue: every Pending request, soonest first. Manager-only
  // (the server 403s anyone else), so it simply doesn't render otherwise.
  const pending = useMemo(
    () =>
      db.res
        .filter((r) => (r.status || "").toLowerCase() === "pending")
        .sort((a, b) => String(a.start || "").localeCompare(String(b.start || ""))),
    [db.res],
  );
  // A Pending request that has since become conflicted is not approvable —
  // the server 409s; this shows why before the click.
  const conflictFor = (r: InvRes): string => {
    const item = db.items.find((i) => i.id === r.itemId);
    const start = asDate(r.start);
    const end = asDate(r.end);
    if (!item || !start || !end) return "";
    return conflictLine(reservationConflicts(item, start, end, r.qty ?? 1, db.tx, db.res, r.id));
  };

  const statusClass = (status?: string) => {
    const v = (status || "").toLowerCase();
    if (v === "approved") return s.chip_available;
    if (v === "denied") return s.chip_retired;
    return s.chip_reserved; // Pending
  };

  return (
    <div>
      <div className={s.toolbar}>
        <select
          className={s.select}
          aria-label="Item"
          value={reserveItemId}
          onChange={(e) => setReserveItemId(e.target.value)}
        >
          <option value="">All items</option>
          {activeItems(db.items).map((i) => (
            <option key={i.id} value={i.id}>{i.name}</option>
          ))}
        </select>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={!reserveItemId}
          title={reserveItemId ? undefined : "Choose an item first"}
          onClick={() => {
            const item = db.items.find((i) => i.id === reserveItemId);
            if (item) setModalItem(item);
          }}
        >
          New reservation
        </button>
        {personalView && (
          <label className={s.toggleLabel}>
            <input type="checkbox" checked={mineOnly} onChange={(e) => setMineOnly(e.target.checked)} />
            Mine only
          </label>
        )}
        <span className={s.spacer} />
        <div className={s.segmented} role="group" aria-label="Reservation view">
          <button type="button" className={`${s.segment} ${view === "list" ? s.segmentActive : ""}`}
            aria-pressed={view === "list"} onClick={() => setView("list")}>List</button>
          <button type="button" className={`${s.segment} ${view === "calendar" ? s.segmentActive : ""}`}
            aria-pressed={view === "calendar"} onClick={() => setView("calendar")}>Calendar</button>
        </div>
      </div>

      {view === "calendar" && (
        <div className={s.panel}>
          <div className={s.plxNav}>
            <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => shiftWeek(-1)}>← Prev</button>
            <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => setAnchor(new Date())}>Today</button>
            <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => shiftWeek(1)}>Next →</button>
            <span className={s.plxWeekLabel}>Week of {calendar.days[0].toLocaleDateString("en-CA")}</span>
          </div>
          <div className={s.plxGridWrap}>
            <div className={s.calGrid} role="table" aria-label="Reservation calendar">
              <div className={s.plxHead}>Item</div>
              {calendar.days.map((d) => (
                <div key={d.toISOString()} className={s.plxHead}>
                  {d.toLocaleDateString(undefined, { weekday: "short", month: "numeric", day: "numeric" })}
                </div>
              ))}
              {calendar.rows.length === 0 ? (
                <div className={s.calEmpty}>No reservations this week.</div>
              ) : (
                calendar.rows.map((row) => (
                  <React.Fragment key={row.itemId}>
                    <div className={s.calItem} role="rowheader">
                      <button type="button" className={s.calItemBtn} onClick={() => onOpenItem(row.itemId)}>
                        {row.name}
                      </button>
                    </div>
                    {row.cells.map((cell, di) => (
                      <div
                        key={`${row.itemId}-${di}`}
                        className={`${s.plxCell} ${calendar.days[di].toDateString() === today ? s.plxToday : ""}`}
                      >
                        {cell.map((r) => (
                          <button
                            key={r.id}
                            type="button"
                            className={`${s.calSpan} ${
                              (r.status || "").toLowerCase() === "approved" ? s.calSpan_approved : s.calSpan_pending
                            }`}
                            title={`${r.user} · ${fmtDateTime(r.start)} → ${fmtDateTime(r.end)} · ${r.status || "Pending"}`}
                            onClick={() => onOpenItem(r.itemId)}
                          >
                            {r.user}{r.qty && r.qty > 1 ? ` ×${r.qty}` : ""}
                          </button>
                        ))}
                      </div>
                    ))}
                  </React.Fragment>
                ))
              )}
            </div>
          </div>
          <div className={s.plxLegend}>
            <span><span className={`${s.chip} ${s.chip_available}`}>Approved</span></span>
            <span><span className={`${s.chip} ${s.chip_reserved}`}>Pending</span></span>
          </div>
        </div>
      )}

      {isManager && pending.length > 0 && (
        <div className={s.panel}>
          <div className={s.panelTitle}>Waiting on you · {pending.length}</div>
          {pending.map((r) => {
            const clash = conflictFor(r);
            return (
            <div key={r.id} className={s.listRow}>
              <div
                style={{ minWidth: 0, cursor: "pointer" }}
                role="button"
                tabIndex={0}
                onClick={() => onOpenItem(r.itemId)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onOpenItem(r.itemId);
                }}
              >
                <div className={s.cellStrong}>{itemName(r.itemId)}</div>
                <div className={s.rowNote}>
                  {r.user || "—"}{r.group ? ` · ${r.group}` : ""} · {fmtDateTime(r.start)} → {fmtDateTime(r.end)}
                  {r.qty && r.qty > 1 ? ` · ×${r.qty}` : ""}{r.purpose ? ` · ${r.purpose}` : ""}
                </div>
                {clash ? <div className={s.dangerText}>{clash}</div> : null}
              </div>
              <span className={s.spacer} />
              <button type="button" className={`${s.btnGhost} ${s.btnSm}`}
                onClick={() => void actions.setReservation(r, itemName(r.itemId), "Denied")}>
                Decline
              </button>
              <button type="button" className={`${s.btnPrimary} ${s.btnSm}`} disabled={!!clash}
                title={clash || undefined}
                onClick={() => void actions.setReservation(r, itemName(r.itemId), "Approved")}>
                Approve
              </button>
            </div>
            );
          })}
        </div>
      )}

      {view === "list" && (
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead>
            <tr>
              <th>Item</th><th>Who</th><th>From</th><th>To</th><th>Status</th><th>Purpose</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={7}><Empty>
                {personalView && mineOnly && db.res.length > 0
                  ? "No reservations of yours here — switch off “Mine only” to see the whole lab."
                  : reservationsEmptyMessage(db.res.length, reserveItemId, itemName(reserveItemId))}
              </Empty></td></tr>
            ) : (
              rows.map((r) => {
                const isPending = (r.status || "").toLowerCase() === "pending";
                // Flag on: Cancel is OWNER-only, compared on the owner KEY
                // (never the display name — duplicate or corrected names
                // must not flip it; a keyless legacy row belongs to nobody
                // until backfilled). Managers clear someone else's booking
                // by denying it, and the server 403s a cancel regardless.
                // Flag off: exactly today's name-equality affordance.
                const mine = personalView
                  ? ownsRowByKey(r, identity)
                  : (r.user || "").toLowerCase() === prefill.name.toLowerCase();
                const canCancel = personalView ? mine : isManager || mine;
                return (
                  <tr key={r.id} className={s.rowBtn} onClick={() => onOpenItem(r.itemId)}>
                    <td className={s.cellStrong}>
                      {itemName(r.itemId)}
                      <div className={s.rowNote}>{r.itemId}</div>
                    </td>
                    <td>{r.user || "—"}{r.group ? <span className={s.muted}> · {r.group}</span> : null}</td>
                    <td className={s.cellNum}>{fmtDateTime(r.start)}</td>
                    <td className={s.cellNum}>{fmtDateTime(r.end)}</td>
                    <td><span className={`${s.chip} ${statusClass(r.status)}`}>{r.status || "Pending"}</span></td>
                    <td className={s.muted}>{r.purpose || "—"}{r.notes ? ` — ${r.notes}` : ""}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {isManager && isPending && (
                        <>
                          <button type="button" className={`${s.btn} ${s.btnSm}`}
                            onClick={() => void actions.setReservation(r, itemName(r.itemId), "Approved")}>
                            Approve
                          </button>{" "}
                          <button type="button" className={`${s.btnGhost} ${s.btnSm}`}
                            onClick={() => void actions.setReservation(r, itemName(r.itemId), "Denied")}>
                            Deny
                          </button>{" "}
                        </>
                      )}
                      {canCancel && (
                        <button type="button" className={`${s.btnGhost} ${s.btnSm}`}
                          onClick={() => {
                            if (window.confirm("Cancel this reservation?")) {
                              void actions.cancelReservation(r, itemName(r.itemId));
                            }
                          }}>
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      )}

      {modalItem && (
        <ReserveModal
          item={modalItem}
          prefill={prefill}
          tx={db.tx}
          res={db.res}
          onClose={() => setModalItem(null)}
          onSubmit={(fields) => {
            void actions.reserve(modalItem, fields);
            setModalItem(null);
          }}
        />
      )}
    </div>
  );
}
