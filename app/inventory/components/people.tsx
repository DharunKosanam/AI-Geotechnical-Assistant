"use client";

/**
 * People & log page: the lab roster (manager CRUD — user edits carry
 * expectedUpdatedAt so concurrent edits 409 into the conflict toast) and the
 * full server-side audit trail.
 */
import React, { useRef, useState } from "react";

import { InventoryActions } from "../actions";
import { toast } from "../../components/toaster";
import s from "../inventory.module.css";
import {
  BackupFile, InvDB, InvUser, RestoreMode, RestoreResult, downloadText, fmtDateTime, invApi, parseBackupFile,
} from "../lib";

/** Audit detail cell: a damage report with a photo renders a thumbnail. */
function AuditDetail({ detail, photosEnabled }: { detail: unknown; photosEnabled: boolean }) {
  if (typeof detail === "string") return <>{detail}</>;
  if (!detail) return <>—</>;
  const d = detail as { type?: string; photoId?: string };
  if (d.photoId && photosEnabled) {
    return (
      <span className={s.listRow} style={{ padding: 0, border: 0 }}>
        <a href={invApi.photoUrl(d.photoId)} target="_blank" rel="noreferrer" title="Open full size">
          <img className={s.thumb} src={invApi.photoUrl(d.photoId)} alt="Damage photo" />
        </a>
        <span>{d.type || "damage"} · photo attached</span>
      </span>
    );
  }
  return <>{JSON.stringify(detail)}</>;
}
import { UserModal } from "./modals";
import { Empty, Field, Modal } from "./ui";

export default function PeoplePage({
  db,
  actions,
  isManager,
  onReload,
  mutate,
  photosEnabled = false,
  personalView = false,
}: {
  db: InvDB;
  actions: InventoryActions;
  isManager: boolean;
  photosEnabled?: boolean;
  /** INVENTORY_PERSONAL_VIEW: flag-on, a roster row without an email cannot
   * be named on an on-behalf write — surfaced HERE, where whoever maintains
   * the roster can fix it, instead of as a 400 at the cupboard. */
  personalView?: boolean;
  /** Full reload after a restore (the server is the only truth then). */
  onReload: () => Promise<void>;
  mutate: (args: {
    apply: (db: InvDB) => InvDB;
    request: () => Promise<unknown>;
    refetch: ("items" | "tx" | "res" | "plaxis" | "users" | "audit")[];
    label: string;
  }) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState<InvUser | null>(null);
  const [adding, setAdding] = useState(false);

  // --- backup / restore (manager-only) ---
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [backup, setBackup] = useState<BackupFile | null>(null);
  const [mode, setMode] = useState<RestoreMode>("merge");
  const [preview, setPreview] = useState<RestoreResult | null>(null);
  const [busy, setBusy] = useState(false);

  const exportBackup = async () => {
    try {
      const b = await invApi.backup();
      downloadText(`linlab-backup-${new Date().toLocaleDateString("en-CA")}.json`, JSON.stringify(b, null, 1));
      toast("Backup downloaded.");
    } catch (e) {
      toast((e as Error).message || "The backup could not be exported.");
    }
  };
  const chooseFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const parsed = parseBackupFile(await file.text());
      setBackup(parsed);
      setPreview(null);
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const dryRun = async (m: RestoreMode) => {
    if (!backup) return;
    setBusy(true);
    try {
      setPreview(await invApi.restore({ backup, mode: m, dryRun: true }));
    } catch (e) {
      toast((e as Error).message || "Dry run failed.");
    } finally {
      setBusy(false);
    }
  };
  const confirmRestore = async () => {
    if (!backup || !preview) return;
    setBusy(true);
    // Through the optimistic runner: nothing to apply optimistically (a
    // restore replaces state wholesale), rollback is a no-op, and the
    // reconcile is a full reload — but errors still surface the same way.
    const ok = await mutate({
      label: "Backup restore",
      refetch: [],
      apply: (d) => d,
      request: () => invApi.restore({ backup, mode: preview.mode, dryRun: false }),
    });
    setBusy(false);
    if (ok) {
      toast(`Restore applied (${preview.mode}).`);
      setBackup(null);
      setPreview(null);
      await onReload();
    }
  };
  const totals = preview
    ? Object.values(preview.diff).reduce(
        (acc, d) => ({ added: acc.added + d.added, changed: acc.changed + d.changed, removed: acc.removed + d.removed }),
        { added: 0, changed: 0, removed: 0 },
      )
    : null;

  return (
    <div>
      <div className={s.panel}>
        <div className={s.toolbar}>
          <div className={s.panelTitle} style={{ marginBottom: 0 }}>Lab members</div>
          <span className={s.spacer} />
          <button type="button" className={s.btnPrimary} onClick={() => setAdding(true)}>
            Add member
          </button>
        </div>
        <div className={s.tableWrap}>
          <table className={`${s.table} ${s.cardTable}`}>
            <thead>
              <tr>
                <th>Name</th><th>Role</th><th>Program</th><th>Group</th>
                <th>Email</th><th>Since</th><th></th>
              </tr>
            </thead>
            <tbody>
              {db.users.length === 0 ? (
                <tr><td colSpan={7}><Empty>No lab members recorded.</Empty></td></tr>
              ) : (
                [...db.users]
                  .sort((a, b) => a.id.localeCompare(b.id))
                  .map((u) => (
                    <tr key={u.id}>
                      <td className={`${s.cellStrong} ${s.cardLead}`}>
                        {u.name}
                        {u.cosup ? <span className={s.muted}> · co-sup {u.cosup}</span> : null}
                      </td>
                      {/* cardStatus = the card's second slot, right after the name */}
                      <td className={s.cardStatus} data-label="Role">{u.role || "—"}</td>
                      <td className={s.muted} data-label="Program">{u.program || "—"}</td>
                      <td className={s.muted} data-label="Group">{u.group || "—"}</td>
                      <td className={s.muted} data-label="Email">
                        {u.email || "—"}
                        {/* A note, not an alert: a roster data gap is neither
                            urgent nor physical, so it stays out of Needs
                            Attention — visible where it can be fixed. */}
                        {personalView && !(u.email || "").trim() ? (
                          <span className={s.rowNote}> · No email — cannot be named on a checkout</span>
                        ) : null}
                      </td>
                      <td className={s.cellNum} data-label="Since">{u.since || "—"}</td>
                      <td className={s.actionsCell}>
                        <button type="button" className={`${s.btnGhost} ${s.btnSm}`}
                          onClick={() => setEditing(u)}>
                          Edit
                        </button>{" "}
                        {/* Remove stays manager-only — irreversible; the
                            server enforces it (403), this only hides it. */}
                        {isManager && (
                          <button type="button" className={`${s.btnGhost} ${s.btnSm}`}
                            onClick={() => {
                              if (window.confirm(`Remove ${u.name} from the roster?`)) {
                                void actions.deleteUser(u);
                              }
                            }}>
                            Remove
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className={s.panel}>
        <div className={s.panelTitle}>Audit log</div>
        {db.audit.length === 0 ? (
          <Empty>No activity recorded yet.</Empty>
        ) : (
          <div className={s.tableWrap}>
            <table className={`${s.table} ${s.cardTable}`}>
              <thead>
                <tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {db.audit.map((a) => (
                  <tr key={a.id}>
                    <td className={s.cellNum} data-label="When">{fmtDateTime(a.ts)}</td>
                    <td className={`${s.cellStrong} ${s.cardLead}`}>
                      {a.actor || "—"}
                      {/* On-behalf / rejected attempts record whose row it
                          was separately from who acted (personal view). */}
                      {a.owner && a.owner !== a.actor ? (
                        <span className={s.muted}> · for {a.owner}</span>
                      ) : null}
                    </td>
                    <td className={s.cardStatus} data-label="Action">{a.action || "—"}</td>
                    <td className={s.cellNum} data-label="Record">{a.entity || "—"}</td>
                    <td className={`${s.muted} ${s.cellWrap}`} data-label="Detail">
                      <AuditDetail detail={a.detail} photosEnabled={photosEnabled} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {isManager && (
        <div className={s.panel}>
          <div className={s.toolbar}>
            <div className={s.panelTitle} style={{ marginBottom: 0 }}>Backup &amp; restore</div>
            <span className={s.spacer} />
            <button type="button" className={s.btn} onClick={() => void exportBackup()}>Export backup</button>
            <input ref={fileRef} type="file" accept="application/json,.json" style={{ display: "none" }}
              onChange={(e) => void chooseFile(e.target.files?.[0])} />
            <button type="button" className={s.btnGhost} onClick={() => fileRef.current?.click()}>Restore from file…</button>
          </div>
          <span className={s.muted}>
            The export is one JSON file (schema v1) holding all six inventory collections. A restore is
            previewed as a dry-run diff and only writes after you confirm — never a blind replace.
          </span>
        </div>
      )}

      {backup && (
        <Modal
          title="Restore backup"
          onClose={() => { setBackup(null); setPreview(null); }}
          actions={
            <>
              <button type="button" className={s.btnGhost} onClick={() => { setBackup(null); setPreview(null); }}>Cancel</button>
              <button type="button" className={s.btn} disabled={busy} onClick={() => void dryRun(mode)}>
                {preview ? "Re-run dry run" : "Dry run"}
              </button>
              <button type="button" className={s.btnDanger} disabled={!preview || busy || preview.mode !== mode}
                onClick={() => void confirmRestore()}>
                Confirm restore
              </button>
            </>
          }
        >
          <span className={s.muted}>
            Backup exported {fmtDateTime(backup.exportedAt)} · schema v{backup.schemaVersion} ·{" "}
            {Object.entries(backup.collections).map(([k, v]) => `${k} ${(v as unknown[]).length}`).join(" · ")}
          </span>
          <Field label="Mode">
            <select className={s.select} value={mode} onChange={(e) => { setMode(e.target.value as RestoreMode); setPreview(null); }}>
              <option value="merge">Merge — add and update by id, never delete</option>
              <option value="replace">Replace — add, update, and delete records absent from the backup</option>
            </select>
          </Field>
          {preview ? (
            <div className={s.tableWrap}>
              <table className={s.table}>
                <thead><tr><th>Collection</th><th>Added</th><th>Changed</th><th>Removed</th><th>Unchanged</th></tr></thead>
                <tbody>
                  {Object.entries(preview.diff).map(([name, d]) => (
                    <tr key={name}>
                      <td className={s.cellStrong}>{name}</td>
                      <td className={s.cellNum}>{d.added}</td>
                      <td className={s.cellNum}>{d.changed}</td>
                      <td className={`${s.cellNum} ${d.removed ? s.dangerText : ""}`}>{d.removed}</td>
                      <td className={s.cellNum}>{d.unchanged}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {totals && (
                <div className={s.rowNote} style={{ padding: "8px 10px" }}>
                  Dry run ({preview.mode}): {totals.added} added · {totals.changed} changed · {totals.removed} removed. Nothing has been written yet.
                </div>
              )}
            </div>
          ) : (
            <span className={s.muted}>Run the dry run to see what would change before confirming.</span>
          )}
        </Modal>
      )}

      {(adding || editing) && (
        <UserModal
          existing={editing}
          onClose={() => { setAdding(false); setEditing(null); }}
          onSubmit={(fields) => {
            void actions.saveUser(editing, fields);
            setAdding(false);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}
