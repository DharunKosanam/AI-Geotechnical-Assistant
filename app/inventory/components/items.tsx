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
  CallerIdentity,
  InvDB,
  InvItem,
  ItemSortKey,
  SessionPrefill,
  STATUS_LABEL,
  SortDir,
  StatusKey,
  activeItems,
  distinctValues,
  filterItems,
  fmtDate,
  invApi,
  isArchived,
  myItemIds,
  nextMaint,
  ownsRow,
  sortItems,
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
  photosEnabled = false,
  personalView = false,
  identity = { email: "", names: [] },
}: {
  db: InvDB;
  actions: InventoryActions;
  prefill: SessionPrefill;
  isManager: boolean;
  photosEnabled?: boolean;
  /** INVENTORY_PERSONAL_VIEW: ownership-gated Return + the "Mine only"
   * filter. Off (the default): everything renders exactly as before. */
  personalView?: boolean;
  identity?: CallerIdentity;
  /** Selection is owned by the tab shell so other pages (Dashboard alerts,
   * Reports rows, Reservations) can deep-link straight into the drawer. */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<"all" | StatusKey>("all");
  const [location, setLocation] = useState("all");
  const [condition, setCondition] = useState("all");
  const [sortKey, setSortKey] = useState<ItemSortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [includeArchived, setIncludeArchived] = useState(false);
  // "Mine only" (INVENTORY_PERSONAL_VIEW): a CLIENT-side filter over the full
  // payload already fetched — never a server-side query parameter, because
  // availability math and "who has the interrogator" need every row. Default
  // ON when the flag is on; the control does not render flag-off.
  const [mineOnly, setMineOnly] = useState(personalView);
  const mineIds = useMemo(
    () => (personalView ? myItemIds(db.tx, db.res, identity) : new Set<string>()),
    [personalView, db.tx, db.res, identity],
  );
  const visibleItems = useMemo(() => {
    const base = includeArchived ? db.items : activeItems(db.items);
    return personalView && mineOnly ? base.filter((i) => mineIds.has(i.id)) : base;
  }, [db.items, includeArchived, personalView, mineOnly, mineIds]);
  const archivedCount = db.items.length - activeItems(db.items).length;
  const setSelectedId = onSelect;
  const [modal, setModal] = useState<ModalKind>(null);

  const categories = useMemo(() => distinctValues(db.items, "category"), [db.items]);
  const locations = useMemo(() => distinctValues(db.items, "location"), [db.items]);
  const conditions = useMemo(() => distinctValues(db.items, "condition"), [db.items]);

  const rows = useMemo(
    () => sortItems(filterItems(visibleItems, { query, category, status, location, condition }), sortKey, sortDir),
    [visibleItems, query, category, status, location, condition, sortKey, sortDir],
  );

  const toggleSort = (key: ItemSortKey) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  };
  const SortTh = ({ col, label }: { col: ItemSortKey; label: string }) => {
    const active = col === sortKey;
    return (
      <th
        aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
        className={s.sortTh}
        onClick={() => toggleSort(col)}
      >
        {label}
        <span className={s.sortIndicator} aria-hidden="true">
          {active ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
        </span>
      </th>
    );
  };

  const selected = db.items.find((i) => i.id === selectedId) || null;
  const openLoans = useMemo(
    () =>
      selected
        ? db.tx.filter((t) => t.type === "checkout" && !t.actualReturn && t.itemId === selected.id)
        : [],
    [db.tx, selected],
  );
  // Ownership gating (INVENTORY_PERSONAL_VIEW): a return acts on ONE person's
  // loan, so only the owner's rows are offered — managers see every loan and
  // the modal labels the on-behalf explicitly ("Return for {name}"). Flag
  // off, every open loan is returnable by anyone, exactly as today; the
  // server enforces the real boundary either way.
  const returnableLoans = useMemo(() => {
    if (!personalView || isManager) return openLoans;
    return openLoans.filter((l) => ownsRow(l, identity));
  }, [personalView, isManager, openLoans, identity]);
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
  const damageReports = useMemo(
    () =>
      selected
        ? db.tx
            .filter((t) => t.type === "damage" && t.itemId === selected.id)
            .sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")))
            .slice(0, 5)
        : [],
    [db.tx, selected],
  );
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
          <select className={s.select} value={location} onChange={(e) => setLocation(e.target.value)} aria-label="Location">
            <option value="all">All locations</option>
            {locations.map((l) => <option key={l}>{l}</option>)}
          </select>
          <select className={s.select} value={condition} onChange={(e) => setCondition(e.target.value)} aria-label="Condition">
            <option value="all">Any condition</option>
            {conditions.map((c) => <option key={c}>{c}</option>)}
          </select>
          <label className={s.toggleLabel}>
            <input type="checkbox" checked={includeArchived} onChange={(e) => setIncludeArchived(e.target.checked)} />
            Include archived{archivedCount ? ` (${archivedCount})` : ""}
          </label>
          {personalView && (
            <label className={s.toggleLabel}>
              <input type="checkbox" checked={mineOnly} onChange={(e) => setMineOnly(e.target.checked)} />
              Mine only
            </label>
          )}
          <span className={s.spacer} />
          <button type="button" className={s.btnPrimary} onClick={() => setModal("new")}>
            New item
          </button>
        </div>

        <div className={s.tableWrap}>
          <table className={`${s.table} ${s.cardTable}`}>
            <thead>
              <tr>
                <SortTh col="name" label="Item" />
                <th>ID</th>
                <SortTh col="qty" label="Qty" />
                <th>Out</th>
                <SortTh col="available" label="Avail" />
                <SortTh col="status" label="Status" />
                <SortTh col="location" label="Location" />
                <SortTh col="custodian" label="Custodian" />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={8}><Empty>
                  {personalView && mineOnly
                    ? "Nothing of yours here — switch off “Mine only” to see the whole lab."
                    : "No items match."}
                </Empty></td></tr>
              ) : (
                rows.map((i) => {
                  const key = statusKey(i);
                  return (
                    <tr
                      key={i.id}
                      className={s.rowBtn}
                      onClick={() => setSelectedId(i.id === selectedId ? null : i.id)}
                    >
                      <td className={`${s.cellStrong} ${s.strata} ${s[`strata_${strataKey(i.category)}`]} ${s.cardLead}`}>
                        {i.name}
                      </td>
                      <td className={s.cellNum} data-label="ID">{i.id}</td>
                      <td className={s.cellNum} data-label="Qty">{i.qty ?? 0}</td>
                      <td className={s.cellNum} data-label="Out">{i.qtyOut ?? 0}</td>
                      <td className={`${s.cellNum} ${s.cardAvail}`} data-label="Avail">
                        {i.kind === "consumable" ? i.qty ?? 0 : (i.qty ?? 0) - (i.qtyOut ?? 0)}
                      </td>
                      <td className={s.cardStatus}><Chip status={key} /></td>
                      <td className={s.muted} data-label="Location">{i.location || "—"}</td>
                      <td className={s.muted} data-label="Custodian">{i.custodian || "—"}</td>
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
            <button
              type="button"
              className={`${s.btnGhost} ${s.btnSm}`}
              onClick={() => setSelectedId(null)}
              aria-label="Close details"
            >
              Close
            </button>
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
            <span className={s.metaKey}>Next service</span>
            <span className={s.metaVal}>
              {selected.nextMaint
                ? fmtDate(selected.nextMaint)
                : nextMaint(selected)?.toLocaleDateString("en-CA") || "—"}
            </span>
            <span className={s.metaKey}>Expiry</span><span className={s.metaVal}>{fmtDate(selected.expiryDate)}</span>
            <span className={s.metaKey}>Description</span>
            <span className={s.metaVal}>{selected.description || "—"}</span>
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
          {damageReports.length > 0 && (
            <div className={s.metaGrid}>
              <span className={s.metaKey}>Damage</span>
              <span className={s.metaVal}>
                {damageReports.map((t) => (
                  <div key={t.id} className={s.listRow} style={{ padding: "4px 0" }}>
                    {t.photoId && photosEnabled ? (
                      <a href={invApi.photoUrl(t.photoId)} target="_blank" rel="noreferrer" title="Open full size">
                        <img className={s.thumb} src={invApi.photoUrl(t.photoId)} alt={`Damage photo for ${selected.name}`} />
                      </a>
                    ) : null}
                    <span>
                      {fmtDate(t.ts)} · {t.user || "—"}{t.condAfter ? ` · ${t.condAfter}` : ""}
                      {t.purpose ? <span className={s.rowNote}> — {t.purpose}</span> : null}
                    </span>
                  </div>
                ))}
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
            <button type="button" className={s.btnPrimary} disabled={avail < 1 || isArchived(selected)}
              onClick={() => setModal("checkout")}>
              Check out
            </button>
            <button type="button" className={s.btn} disabled={returnableLoans.length === 0}
              onClick={() => setModal("return")}>
              Return
            </button>
            <button type="button" className={s.btn} disabled={isArchived(selected)} onClick={() => setModal("reserve")}>Reserve</button>
            <button type="button" className={s.btn} onClick={() => setModal("adjust")}>Adjust</button>
            <button type="button" className={s.btnGhost} onClick={() => setModal("damage")}>Damage</button>
            <button type="button" className={s.btnGhost} onClick={() => setModal("edit")}>Edit</button>
            {/* Records are never deleted: managers ARCHIVE (reversible, audited).
                The server enforces it (403 / DELETE is 405); this only hides it. */}
            {isManager && (
              isArchived(selected) ? (
                <button type="button" className={s.btn} onClick={() => void actions.restoreItem(selected)}>
                  Restore
                </button>
              ) : (
                <button
                  type="button"
                  className={s.btnDanger}
                  onClick={() => {
                    if (window.confirm(`Archive "${selected.name}"? It leaves the default view, KPIs and alerts and cannot be checked out or reserved until restored.`)) {
                      void actions.archiveItem(selected);
                    }
                  }}
                >
                  Archive
                </button>
              )
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
        <ReturnModal item={selected} loans={returnableLoans} onClose={close}
          submitLabel={(loan) =>
            personalView && !ownsRow(loan, identity)
              ? `Return for ${loan.user || "borrower"}`
              : "Record return"}
          onSubmit={(loan, condAfter) => { void actions.returnLoan(selected, loan, condAfter); close(); }} />
      )}
      {selected && modal === "adjust" && (
        <AdjustModal item={selected} onClose={close}
          onSubmit={(delta, note) => { void actions.adjust(selected, delta, note, prefill.name); close(); }} />
      )}
      {selected && modal === "damage" && (
        <DamageModal item={selected} photosEnabled={photosEnabled} onClose={close}
          onSubmit={(condAfter, note, photo) => { void actions.damage(selected, condAfter, note, prefill.name, photo); close(); }} />
      )}
      {selected && modal === "edit" && (
        <ItemModal item={selected} onClose={close}
          onSubmit={(fields) => { void actions.editItem(selected, fields); close(); }} />
      )}
      {selected && modal === "reserve" && (
        <ReserveModal item={selected} prefill={prefill} tx={db.tx} res={db.res} onClose={close}
          onSubmit={(fields) => { void actions.reserve(selected, fields); close(); }} />
      )}
    </div>
  );
}
