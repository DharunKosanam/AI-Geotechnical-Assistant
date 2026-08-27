"use client";

/**
 * Reports: five sections, each with its own CSV export routed through the
 * Export modal (preview + row count before download). Every table and its
 * CSV come from the same pure builder in lib.ts, so they can never drift.
 * "Days overdue" is the server-clock value from the alerts payload where one
 * exists (overdueRows), so it agrees with the Dashboard.
 */
import React, { useMemo, useState } from "react";

import s from "../inventory.module.css";
import {
  ExportPayload,
  InvDB,
  Report,
  availabilityByCategory,
  availableQty,
  buildExport,
  checkoutFrequency,
  fmtDate,
  inventoryReport,
  lowStockReport,
  mostBorrowedReport,
  nextMaint,
  overdueReport,
  overdueRows,
  serviceItems,
  serviceReport,
  statusKey,
  strataKey,
} from "../lib";
import { ExportModal } from "./modals";
import { Chip, Empty } from "./ui";

// Export buttons name their CONTENT, never a format: the Export modal
// offers CSV and XLSX, so "Export CSV" would be wrong on both counts.
function SectionHead({
  title,
  onExport,
  exportLabel,
}: {
  title: string;
  onExport: () => void;
  exportLabel: string;
}) {
  return (
    <div className={s.sectionHead}>
      <span className={s.sectionTitle}>{title}</span>
      <span className={s.sectionRule} />
      <button type="button" className={`${s.btn} ${s.btnSm}`} onClick={onExport}>
        {exportLabel}
      </button>
    </div>
  );
}

export default function ReportsPage({
  db,
  onOpenItem,
}: {
  db: InvDB;
  onOpenItem: (itemId: string) => void;
}) {
  const [exportPayload, setExportPayload] = useState<ExportPayload | null>(null);
  const open = (report: Report) => setExportPayload(buildExport(report));

  const byCategory = useMemo(() => availabilityByCategory(db.items), [db.items]);
  const frequency = useMemo(() => checkoutFrequency(db.tx).slice(0, 8), [db.tx]);
  const maxFreq = Math.max(1, ...frequency.map((f) => f.count));
  const overdue = useMemo(() => overdueRows(db.tx, db.items, db.alerts), [db.tx, db.items, db.alerts]);
  const consumables = useMemo(
    () => db.items.filter((i) => i.kind === "consumable").sort((a, b) => a.id.localeCompare(b.id)),
    [db.items],
  );
  const service = useMemo(() => serviceItems(db.items).sort((a, b) => a.id.localeCompare(b.id)), [db.items]);
  const itemName = (id: string) => db.items.find((i) => i.id === id)?.name || id;
  const rowProps = (itemId: string) => ({
    className: `${s.rowBtn}`,
    onClick: () => onOpenItem(itemId),
  });

  return (
    <div>
      {/* 1 ─ Availability by category */}
      <SectionHead
        title="Availability by category"
        exportLabel="Export availability"
        onExport={() => open(inventoryReport(db.items))}
      />
      <div className={s.tableWrap}>
        <table className={`${s.table} ${s.cardTable}`}>
          <thead>
            <tr><th>Category</th><th>Records</th><th>Available</th><th>In use / out</th><th>Maintenance</th><th>Missing</th></tr>
          </thead>
          <tbody>
            {byCategory.map((c) => (
              <tr key={c.category}>
                <td className={`${s.cellStrong} ${s.cardLead}`}>{c.category}</td>
                <td className={s.cellNum} data-label="Records">{c.records}</td>
                <td className={`${s.cellNum} ${s.kpiValue_accent} ${s.cardAvail}`} data-label="Available">{c.available}</td>
                <td className={s.cellNum} data-label="In use">{c.inUse}</td>
                <td className={`${s.cellNum} ${c.maintenance ? s.warnText : ""}`} data-label="Maintenance">{c.maintenance}</td>
                <td className={`${s.cellNum} ${c.missing ? s.dangerText : ""}`} data-label="Missing">{c.missing}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={s.twoCol}>
        {/* 2 ─ Most borrowed */}
        <div>
          <SectionHead title="Most borrowed" exportLabel="Export most borrowed" onExport={() => open(mostBorrowedReport(db.tx, db.items))} />
          <div className={s.panel}>
            {frequency.length === 0 ? (
              <Empty>No check-outs yet.</Empty>
            ) : (
              frequency.map((f) => (
                <div
                  key={f.itemId}
                  className={`${s.listRow} ${s.linkRow}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => onOpenItem(f.itemId)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") onOpenItem(f.itemId);
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className={s.toolbar} style={{ marginBottom: 0 }}>
                      <span className={s.cellStrong}>{itemName(f.itemId)}</span>
                      <span className={s.spacer} />
                      <span className={s.cellNum}>{f.count}×</span>
                    </div>
                    <div className={s.bar}>
                      <span className={s.barFill} style={{ width: `${(f.count / maxFreq) * 100}%` }} />
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* 3 ─ Overdue */}
        <div>
          <SectionHead title="Overdue" exportLabel="Export overdue" onExport={() => open(overdueReport(db.tx, db.items, db.alerts))} />
          <div className={s.panel}>
            {overdue.length === 0 ? (
              <Empty>Nothing overdue — every loan is inside its return window.</Empty>
            ) : (
              overdue.map((r) => (
                <div
                  key={r.txId}
                  className={`${s.listRow} ${s.linkRow}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => onOpenItem(r.itemId)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") onOpenItem(r.itemId);
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div className={s.cellStrong}>{r.item}</div>
                    <div className={s.rowNote}>
                      {r.user}{r.email ? ` · ${r.email}` : ""}{r.group ? ` · ${r.group}` : ""} · {r.qty} taken {r.taken}, due {r.due}
                    </div>
                  </div>
                  <span className={s.spacer} />
                  <span className={`${s.chip} ${s.chip_missing}`}>{r.daysOverdue} d late</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* 4 ─ Low stock and consumable usage */}
      <SectionHead
        title="Low stock and consumable usage"
        exportLabel="Export order list"
        onExport={() => open(lowStockReport(db.items))}
      />
      <div className={s.tableWrap}>
        <table className={`${s.table} ${s.cardTable}`}>
          <thead>
            <tr><th>Item</th><th>On hand</th><th>Minimum</th><th>Supplier</th><th>Location</th><th>Expiry</th></tr>
          </thead>
          <tbody>
            {consumables.length === 0 ? (
              <tr><td colSpan={6}><Empty>No consumables tracked.</Empty></td></tr>
            ) : (
              consumables.map((i) => {
                const low = typeof i.minStock === "number" && availableQty(i) <= i.minStock;
                return (
                  <tr key={i.id} {...rowProps(i.id)}>
                    <td className={`${s.cellStrong} ${s.strata} ${low ? s.strata_daq : s.strata_fiber} ${s.cardLead}`}>{i.name}</td>
                    <td className={`${s.cellNum} ${low ? s.dangerText : ""} ${s.cardAvail}`} data-label="On hand">{i.qty ?? 0} {i.unit || ""}</td>
                    <td className={s.cellNum} data-label="Minimum">{i.minStock ?? 0}</td>
                    <td className={s.muted} data-label="Supplier">{i.supplier || "—"}</td>
                    <td className={s.muted} data-label="Location">{i.location || "—"}</td>
                    <td className={s.muted} data-label="Expiry">{fmtDate(i.expiryDate)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 5 ─ Damaged, missing and in service */}
      <SectionHead title="Damaged, missing and in service" exportLabel="Export damaged" onExport={() => open(serviceReport(db.items))} />
      <div className={s.tableWrap}>
        <table className={`${s.table} ${s.cardTable}`}>
          <thead>
            <tr><th>Item</th><th>Status</th><th>Condition</th><th>Location</th><th>Next service</th></tr>
          </thead>
          <tbody>
            {service.length === 0 ? (
              <tr><td colSpan={5}><Empty>Everything is serviceable.</Empty></td></tr>
            ) : (
              service.map((i) => {
                const next = nextMaint(i);
                return (
                  <tr key={i.id} {...rowProps(i.id)}>
                    <td className={`${s.cellStrong} ${s.strata} ${s[`strata_${strataKey(i.category)}`]} ${s.cardLead}`}>
                      {i.name}
                      {i.notes ? <div className={s.rowNote}>{i.notes.slice(0, 70)}</div> : null}
                    </td>
                    <td className={s.cardStatus}><Chip status={statusKey(i)} /></td>
                    <td className={s.muted} data-label="Condition">{i.condition || "—"}</td>
                    <td className={s.muted} data-label="Location">{i.location || "—"}</td>
                    <td className={s.cellNum} data-label="Next service">{next ? next.toLocaleDateString("en-CA") : "—"}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {exportPayload && <ExportModal payload={exportPayload} onClose={() => setExportPayload(null)} />}
    </div>
  );
}
