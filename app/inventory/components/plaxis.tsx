"use client";

/**
 * PLAXIS seats page: the weekly grid (bookingsAt slots the sessions into
 * day × hour cells, seats differentiated by hue), stale-seat detection
 * (held past booked end without logging out — flagged and surfaced first),
 * session start/end. Ending a session is a PUT with expectedUpdatedAt, so
 * two people closing the same stale seat resolves as a 409, not a clobber.
 */
import React, { useMemo, useState } from "react";

import { InventoryActions } from "../actions";
import s from "../inventory.module.css";
import {
  InvDB,
  SessionPrefill,
  bookingsAt,
  fmtDateTime,
  isStaleSeat,
  weekDays,
} from "../lib";
import { PlaxisModal } from "./modals";
import { Empty } from "./ui";

// 07:00 – 22:00, matching the prototype's PX_START/PX_END (last row 21:00).
const HOURS = Array.from({ length: 15 }, (_, i) => 7 + i);

export default function PlaxisPage({
  db,
  actions,
  prefill,
  isManager,
}: {
  db: InvDB;
  actions: InventoryActions;
  prefill: SessionPrefill;
  isManager: boolean;
}) {
  const [anchor, setAnchor] = useState(() => new Date());
  const [booking, setBooking] = useState(false);
  const now = new Date();
  const days = useMemo(() => weekDays(anchor), [anchor]);
  const active = db.plaxis.filter((p) => !p.loggedOut);
  const gridSessions = active; // logged-out sessions leave the grid

  const shiftWeek = (delta: number) =>
    setAnchor((a) => new Date(a.getTime() + delta * 7 * 86_400_000));

  const dayLabel = (d: Date) =>
    d.toLocaleDateString(undefined, { weekday: "short", month: "numeric", day: "numeric" });

  return (
    <div>
      <div className={s.plxNav}>
        <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => shiftWeek(-1)}>
          ← Prev
        </button>
        <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => setAnchor(new Date())}>
          Today
        </button>
        <button type="button" className={`${s.btnGhost} ${s.btnSm}`} onClick={() => shiftWeek(1)}>
          Next →
        </button>
        <span className={s.plxWeekLabel}>
          Week of {days[0].toLocaleDateString("en-CA")}
        </span>
        <span className={s.spacer} />
        <button type="button" className={s.btnPrimary} onClick={() => setBooking(true)}>
          Book a seat
        </button>
      </div>

      <div className={s.plxGridWrap}>
        <div className={s.plxGrid} role="table" aria-label="PLAXIS weekly seat grid">
          <div className={s.plxHead} />
          {days.map((d) => (
            <div key={d.toISOString()} className={s.plxHead}>{dayLabel(d)}</div>
          ))}
          {HOURS.map((h) => (
            <React.Fragment key={h}>
              <div className={s.plxHour}>{String(h).padStart(2, "0")}:00</div>
              {days.map((d) => {
                const slot = bookingsAt(gridSessions, d, h);
                const isToday = d.toDateString() === now.toDateString();
                return (
                  <div key={`${d.toISOString()}-${h}`} className={`${s.plxCell} ${isToday ? s.plxToday : ""}`}>
                    {slot.map((b) => (
                      <span
                        key={b.id}
                        className={`${s.plxBooking} ${
                          isStaleSeat(b, now) ? s.plxStale : b.seat === 1 ? s.plxSeat2 : s.plxSeat1
                        }`}
                        title={`Seat ${(b.seat ?? 0) + 1} · ${b.user}${b.purpose ? ` — ${b.purpose}` : ""}${
                          isStaleSeat(b, now) ? " (held past end)" : ""
                        }`}
                      >
                        S{(b.seat ?? 0) + 1} {b.user}
                      </span>
                    ))}
                  </div>
                );
              })}
            </React.Fragment>
          ))}
        </div>
      </div>

      <div className={s.plxLegend}>
        <span><span className={s.plxSwatch} style={{ background: "var(--accent-a)", borderLeft: "2px solid var(--accent)" }} /> Seat 1</span>
        <span><span className={s.plxSwatch} style={{ background: "color-mix(in srgb, var(--oxide) 13%, transparent)", borderLeft: "2px solid var(--oxide)" }} /> Seat 2</span>
        <span><span className={s.plxSwatch} style={{ background: "var(--danger-a)", borderLeft: "2px solid var(--danger)" }} /> Held past end</span>
      </div>

      <div className={s.panel} style={{ marginTop: 16 }}>
        <div className={s.panelTitle}>Sessions</div>
        {active.length === 0 ? (
          <Empty>Both seats are free.</Empty>
        ) : (
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr><th>Seat</th><th>Who</th><th>From</th><th>To</th><th>Purpose</th><th></th></tr>
              </thead>
              <tbody>
                {[...active]
                  .sort((a, b) => Number(isStaleSeat(b, now)) - Number(isStaleSeat(a, now)))
                  .map((p) => {
                    const stale = isStaleSeat(p, now);
                    const mine = (p.user || "").toLowerCase() === prefill.name.toLowerCase();
                    return (
                      <tr key={p.id}>
                        <td className={s.cellNum}>Seat {(p.seat ?? 0) + 1}</td>
                        <td className={s.cellStrong}>{p.user}</td>
                        <td className={s.cellNum}>{fmtDateTime(p.start)}</td>
                        <td className={stale ? s.dangerText : s.cellNum}>
                          {fmtDateTime(p.end)}{stale ? " · held past end" : ""}
                        </td>
                        <td className={s.muted}>{p.purpose || "—"}</td>
                        <td>
                          {(mine || isManager) && (
                            <button type="button" className={`${s.btn} ${s.btnSm}`}
                              onClick={() => void actions.endPlaxis(p)}>
                              Log out
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {booking && (
        <PlaxisModal
          sessions={db.plaxis}
          prefill={prefill}
          onClose={() => setBooking(false)}
          onSubmit={(fields) => {
            void actions.startPlaxis(fields);
            setBooking(false);
          }}
        />
      )}
    </div>
  );
}
