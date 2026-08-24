"use client";

/**
 * Reservations page: upcoming + recent requests, manager approve/deny
 * (PUT with expectedUpdatedAt — a concurrent decision 409s into the conflict
 * toast, never a silent overwrite), cancel, and a new-request flow.
 */
import React, { useMemo, useState } from "react";

import { InventoryActions } from "../actions";
import s from "../inventory.module.css";
import { InvDB, InvItem, SessionPrefill, fmtDateTime } from "../lib";
import { ReserveModal } from "./modals";
import { Empty } from "./ui";

export default function ReservationsPage({
  db,
  actions,
  prefill,
  isManager,
  onOpenItem,
}: {
  db: InvDB;
  actions: InventoryActions;
  prefill: SessionPrefill;
  isManager: boolean;
  onOpenItem: (itemId: string) => void;
}) {
  const [reserveItemId, setReserveItemId] = useState(db.items[0]?.id || "");
  const [modalItem, setModalItem] = useState<InvItem | null>(null);

  const itemName = (id: string) => db.items.find((i) => i.id === id)?.name || id;

  const rows = useMemo(
    () =>
      [...db.res].sort((a, b) => String(b.start || "").localeCompare(String(a.start || ""))),
    [db.res],
  );
  // Approve/deny queue: every Pending request, soonest first. Manager-only
  // (the server 403s anyone else), so it simply doesn't render otherwise.
  const pending = useMemo(
    () =>
      db.res
        .filter((r) => (r.status || "").toLowerCase() === "pending")
        .sort((a, b) => String(a.start || "").localeCompare(String(b.start || ""))),
    [db.res],
  );

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
          value={reserveItemId}
          onChange={(e) => setReserveItemId(e.target.value)}
        >
          {db.items.map((i) => (
            <option key={i.id} value={i.id}>{i.name}</option>
          ))}
        </select>
        <button
          type="button"
          className={s.btnPrimary}
          disabled={!reserveItemId}
          onClick={() => {
            const item = db.items.find((i) => i.id === reserveItemId);
            if (item) setModalItem(item);
          }}
        >
          New reservation
        </button>
      </div>

      {isManager && pending.length > 0 && (
        <div className={s.panel}>
          <div className={s.panelTitle}>Waiting on you · {pending.length}</div>
          {pending.map((r) => (
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
                  {r.purpose ? ` · ${r.purpose}` : ""}
                </div>
              </div>
              <span className={s.spacer} />
              <button type="button" className={`${s.btnGhost} ${s.btnSm}`}
                onClick={() => void actions.setReservation(r, itemName(r.itemId), "Denied")}>
                Decline
              </button>
              <button type="button" className={`${s.btnPrimary} ${s.btnSm}`}
                onClick={() => void actions.setReservation(r, itemName(r.itemId), "Approved")}>
                Approve
              </button>
            </div>
          ))}
        </div>
      )}

      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead>
            <tr>
              <th>Item</th><th>Who</th><th>From</th><th>To</th><th>Status</th><th>Purpose</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={7}><Empty>No reservations yet.</Empty></td></tr>
            ) : (
              rows.map((r) => {
                const isPending = (r.status || "").toLowerCase() === "pending";
                const mine =
                  (r.user || "").toLowerCase() === prefill.name.toLowerCase();
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
                      {(isManager || mine) && (
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

      {modalItem && (
        <ReserveModal
          item={modalItem}
          prefill={prefill}
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
