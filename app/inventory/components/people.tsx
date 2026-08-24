"use client";

/**
 * People & log page: the lab roster (manager CRUD — user edits carry
 * expectedUpdatedAt so concurrent edits 409 into the conflict toast) and the
 * full server-side audit trail.
 */
import React, { useState } from "react";

import { InventoryActions } from "../actions";
import s from "../inventory.module.css";
import { InvDB, InvUser, fmtDateTime } from "../lib";
import { UserModal } from "./modals";
import { Empty } from "./ui";

export default function PeoplePage({
  db,
  actions,
  isManager,
}: {
  db: InvDB;
  actions: InventoryActions;
  isManager: boolean;
}) {
  const [editing, setEditing] = useState<InvUser | null>(null);
  const [adding, setAdding] = useState(false);

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
          <table className={s.table}>
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
                      <td className={s.cellStrong}>
                        {u.name}
                        {u.cosup ? <span className={s.muted}> · co-sup {u.cosup}</span> : null}
                      </td>
                      <td>{u.role || "—"}</td>
                      <td className={s.muted}>{u.program || "—"}</td>
                      <td className={s.muted}>{u.group || "—"}</td>
                      <td className={s.muted}>{u.email || "—"}</td>
                      <td className={s.cellNum}>{u.since || "—"}</td>
                      <td>
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
            <table className={s.table}>
              <thead>
                <tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {db.audit.map((a) => (
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
