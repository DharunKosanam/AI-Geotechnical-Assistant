"use client";

/**
 * Inventory page: filterable item table (strata rail by category, status
 * chips) + a detail drawer with the transactional actions. All writes go
 * through the actions layer (optimistic + rollback + 409 handling); the
 * server's refetched values overwrite the optimistic ones on success.
 */
import React, { useMemo, useState } from "react";

import { InventoryActions } from "../actions";
import s from "../inventory.module.css";
import {
  InvDB,
  InvItem,
  SessionPrefill,
  STATUS_LABEL,
  StatusKey,
  fmtDate,
  statusKey,
  strataKey,
} from "../lib";
import {
  AdjustModal,
  CheckoutModal,
  DamageModal,
  ItemModal,
  ReserveModal,
  ReturnModal,
} from "./modals";
import { Chip, Empty } from "./ui";

type ModalKind = "checkout" | "return" | "adjust" | "damage" | "edit" | "new" | "reserve" | null;

export default function ItemsPage({
  db,
  actions,
  prefill,
  isManager,
  selectedId,
  onSelect,
}: {
  db: InvDB;
  actions: InventoryActions;
  prefill: SessionPrefill;
  isManager: boolean;
  /** Selection is owned by the tab shell so other pages (Dashboard alerts,
   * Reports rows, Reservations) can deep-link straight into the drawer. */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<"all" | StatusKey>("all");
  const setSelectedId = onSelect;
  const [modal, setModal] = useState<ModalKind>(null);

  const categories = useMemo(
    () => Array.from(new Set(db.items.map((i) => i.category || "Uncategorised"))).sort(),
    [db.items],
  );

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return db.items
      .filter((i) => {
        if (category !== "all" && (i.category || "Uncategorised") !== category) return false;
        if (status !== "all" && statusKey(i) !== status) return false;
        if (!q) return true;
        return [i.name, i.id, i.model, i.serial, i.location, i.custodian, i.notes]
          .some((v) => (v || "").toLowerCase().includes(q));
      })
      .sort((a, b) => a.id.localeCompare(b.id));
  }, [db.items, query, category, status]);

  const selected = db.items.find((i) => i.id === selectedId) || null;
  const openLoans = useMemo(
    () =>
      selected
        ? db.tx.filter((t) => t.type === "checkout" && !t.actualReturn && t.itemId === selected.id)
        : [],
    [db.tx, selected],
  );
  // Per-item reservation grouping: this item's upcoming (not denied, not
  // yet ended) reservations, soonest first.
  const upcomingRes = useMemo(() => {
    if (!selected) return [];
    const now = Date.now();
    return db.res
      .filter((r) => r.itemId === selected.id && (r.status || "").toLowerCase() !== "denied")
      .filter((r) => !r.end || new Date(r.end).getTime() >= now)
      .sort((a, b) => String(a.start || "").localeCompare(String(b.start || "")));
  }, [db.res, selected]);
  const avail = selected
    ? selected.kind === "consumable"
      ? selected.qty ?? 0
      : (selected.qty ?? 0) - (selected.qtyOut ?? 0)
    : 0;

  const close = () => setModal(null);

  return (
    <div className={s.layoutSplit}>
      <div className={s.layoutMain}>
        <div className={s.toolbar}>
          <input
            className={s.searchInput}
            placeholder="Search name, id, serial, location…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select className={s.select} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">All categories</option>
            {categories.map((c) => <option key={c}>{c}</option>)}
          </select>
          <select
            className={s.select}
            value={status}
            onChange={(e) => setStatus(e.target.value as "all" | StatusKey)}
          >
            <option value="all">All statuses</option>
            {(Object.keys(STATUS_LABEL) as StatusKey[]).map((k) => (
              <option key={k} value={k}>{STATUS_LABEL[k]}</option>
            ))}
          </select>
          <span className={s.spacer} />
          <button type="button" className={s.btnPrimary} onClick={() => setModal("new")}>
            New item
          </button>
        </div>

        <div className={s.tableWrap}>
          <table className={s.table}>
            <thead>
              <tr>
                <th>Item</th><th>ID</th><th>Qty</th><th>Out</th><th>Avail</th>
                <th>Status</th><th>Location</th><th>Custodian</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={8}><Empty>No items match.</Empty></td></tr>
              ) : (
                rows.map((i) => {
                  const key = statusKey(i);
                  return (
                    <tr
                      key={i.id}
                      className={s.rowBtn}
                      onClick={() => setSelectedId(i.id === selectedId ? null : i.id)}
                    >
                      <td className={`${s.cellStrong} ${s.strata} ${s[`strata_${strataKey(i.category)}`]}`}>
                        {i.name}
                      </td>
                      <td className={s.cellNum}>{i.id}</td>
                      <td className={s.cellNum}>{i.qty ?? 0}</td>
                      <td className={s.cellNum}>{i.qtyOut ?? 0}</td>
                      <td className={s.cellNum}>
                        {i.kind === "consumable" ? i.qty ?? 0 : (i.qty ?? 0) - (i.qtyOut ?? 0)}
                      </td>
                      <td><Chip status={key} /></td>
                      <td className={s.muted}>{i.location || "—"}</td>
                      <td className={s.muted}>{i.custodian || "—"}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <aside className={s.drawer} aria-label={`Details for ${selected.name}`}>
          <div className={s.drawerHead}>
            <h3 className={s.drawerName}>{selected.name}</h3>
            <Chip status={statusKey(selected)} />
          </div>
          <div className={s.metaGrid}>
            <span className={s.metaKey}>ID</span><span className={s.metaVal}>{selected.id}</span>
            <span className={s.metaKey}>Category</span>
            <span className={s.metaVal}>{selected.category || "—"}{selected.subCategory ? ` · ${selected.subCategory}` : ""}</span>
            <span className={s.metaKey}>Make</span>
            <span className={s.metaVal}>
              {[selected.manufacturer, selected.model].filter(Boolean).join(" ") || "—"}
            </span>
            <span className={s.metaKey}>Serial</span><span className={s.metaVal}>{selected.serial || "—"}</span>
            <span className={s.metaKey}>Stock</span>
            <span className={s.metaVal}>
              {selected.qty ?? 0} {selected.unit || ""} · {selected.qtyOut ?? 0} out · {avail} available
            </span>
            <span className={s.metaKey}>Condition</span><span className={s.metaVal}>{selected.condition || "—"}</span>
            <span className={s.metaKey}>Location</span><span className={s.metaVal}>{selected.location || "—"}</span>
            <span className={s.metaKey}>Custodian</span><span className={s.metaVal}>{selected.custodian || "—"}</span>
            <span className={s.metaKey}>Last maint</span><span className={s.metaVal}>{fmtDate(selected.lastMaint)}</span>
            <span className={s.metaKey}>Expiry</span><span className={s.metaVal}>{fmtDate(selected.expiryDate)}</span>
            {selected.notes ? (
              <>
                <span className={s.metaKey}>Notes</span>
                <span className={s.metaVal}>{selected.notes}</span>
              </>
            ) : null}
          </div>
          {openLoans.length > 0 && (
            <div className={s.metaGrid}>
              <span className={s.metaKey}>Out with</span>
              <span className={s.metaVal}>
                {openLoans.map((l) => `${l.user} (${l.qty ?? 1}, due ${fmtDate(l.expectedReturn)})`).join("; ")}
              </span>
            </div>
          )}
          {upcomingRes.length > 0 && (
            <div className={s.metaGrid}>
              <span className={s.metaKey}>Reserved</span>
              <span className={s.metaVal}>
                {upcomingRes.map((r) => (
                  <div key={r.id}>
                    {r.user} · {fmtDate(r.start)} → {fmtDate(r.end)}
                    <span className={s.rowNote}> ({r.status || "Pending"}{r.purpose ? ` · ${r.purpose}` : ""})</span>
                  </div>
                ))}
              </span>
            </div>
          )}
          <div className={s.drawerActions}>
            <button type="button" className={s.btnPrimary} disabled={avail < 1}
              onClick={() => setModal("checkout")}>
              Check out
            </button>
            <button type="button" className={s.btn} disabled={openLoans.length === 0}
              onClick={() => setModal("return")}>
              Return
            </button>
            <button type="button" className={s.btn} onClick={() => setModal("reserve")}>Reserve</button>
            <button type="button" className={s.btn} onClick={() => setModal("adjust")}>Adjust</button>
            <button type="button" className={s.btnGhost} onClick={() => setModal("damage")}>Damage</button>
            <button type="button" className={s.btnGhost} onClick={() => setModal("edit")}>Edit</button>
            {/* Delete stays manager-only — the one action the audit log can't
                reverse. The server enforces it (403); this only hides it. */}
            {isManager && (
              <button
                type="button"
                className={s.btnDanger}
                onClick={() => {
                  if (window.confirm(`Delete "${selected.name}" from the inventory?`)) {
                    void actions.deleteItem(selected);
                    setSelectedId(null);
                  }
                }}
              >
                Delete
              </button>
            )}
          </div>
        </aside>
      )}

      {modal === "new" && (
        <ItemModal item={null} onClose={close}
          onSubmit={(fields) => { void actions.createItem(fields); close(); }} />
      )}
      {selected && modal === "checkout" && (
        <CheckoutModal item={selected} prefill={prefill} roster={db.users} onClose={close}
          onSubmit={(form) => { void actions.checkout(selected, form); close(); }} />
      )}
      {selected && modal === "return" && (
        <ReturnModal item={selected} loans={openLoans} onClose={close}
          onSubmit={(loan, condAfter) => { void actions.returnLoan(selected, loan, condAfter); close(); }} />
      )}
      {selected && modal === "adjust" && (
        <AdjustModal item={selected} onClose={close}
          onSubmit={(delta, note) => { void actions.adjust(selected, delta, note, prefill.name); close(); }} />
      )}
      {selected && modal === "damage" && (
        <DamageModal item={selected} onClose={close}
          onSubmit={(condAfter, note) => { void actions.damage(selected, condAfter, note, prefill.name); close(); }} />
      )}
      {selected && modal === "edit" && (
        <ItemModal item={selected} onClose={close}
          onSubmit={(fields) => { void actions.editItem(selected, fields); close(); }} />
      )}
      {selected && modal === "reserve" && (
        <ReserveModal item={selected} prefill={prefill} onClose={close}
          onSubmit={(fields) => { void actions.reserve(selected, fields); close(); }} />
      )}
    </div>
  );
}
