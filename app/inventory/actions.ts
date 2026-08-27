"use client";

/**
 * One function per user-visible mutation, each mapped to its Phase-2
 * endpoint (verified against python_backend/app/routers/inventory.py):
 *
 *   checkout          POST /api/inventory/tx   {type:"checkout"}   (server: qtyOut+/consumable qty-)
 *   returnLoan        POST /api/inventory/tx   {type:"return", closesTxId}
 *   adjust            POST /api/inventory/tx   {type:"adjust", qty:<delta>}
 *   damage            POST /api/inventory/tx   {type:"damage", condAfter, photoId?}
 *                     (optional photo: POST /api/inventory/photos first, INVENTORY_PHOTOS_ENABLED)
 *   createItem        POST /api/inventory/items
 *   editItem          PUT  /api/inventory/items/{id}      + expectedUpdatedAt
 *   archiveItem       POST /api/inventory/items/{id}/archive   (manager; DELETE /items is 405)
 *   restoreItem       POST /api/inventory/items/{id}/restore
 *   reserve           POST /api/inventory/res
 *   setReservation    PUT  /api/inventory/res/{id}        + expectedUpdatedAt (approve/deny)
 *   cancelReservation DELETE /api/inventory/res/{id}
 *   saveUser          POST/PUT /api/inventory/users(/{id}) + expectedUpdatedAt
 *   deleteUser        DELETE /api/inventory/users/{id}
 *   startPlaxis       POST /api/inventory/plaxis
 *   endPlaxis         PUT  /api/inventory/plaxis/{id} {loggedOut:true} + expectedUpdatedAt
 *
 * Every call goes through the store's mutate(): optimistic apply, rollback on
 * failure, 409 conflict toast + refetch, then a refetch reconcile — the
 * server stays authoritative on qtyOut / status / audit / alerts.
 */
import {
  InvDB,
  InvItem,
  InvPlaxis,
  InvRes,
  InvTx,
  InvUser,
  invApi,
} from "./lib";

type Mutate = (args: {
  apply: (db: InvDB) => InvDB;
  request: () => Promise<unknown>;
  refetch: ("items" | "tx" | "res" | "plaxis" | "users" | "audit")[];
  label: string;
}) => Promise<boolean>;

// studentId and group are deliberately absent: the server resolves both from
// the inv_users roster via the session user's email at write time — a
// client-supplied value would be overwritten anyway.
export type CheckoutForm = {
  user: string;
  email: string;
  qty: number;
  expectedReturn: string; // ISO date
  purpose: string;
};

const tempId = () => `tmp-${Math.random().toString(36).slice(2, 10)}`;

function patchItem(db: InvDB, id: string, patch: Partial<InvItem>): InvDB {
  return { ...db, items: db.items.map((i) => (i.id === id ? { ...i, ...patch } : i)) };
}

export function makeActions(mutate: Mutate) {
  return {
    checkout(item: InvItem, form: CheckoutForm) {
      const optimistic: InvTx = {
        id: tempId(),
        itemId: item.id,
        type: "checkout",
        user: form.user,
        email: form.email,
        qty: form.qty,
        ts: new Date().toISOString(),
        expectedReturn: form.expectedReturn || null,
        condBefore: item.condition,
        purpose: form.purpose,
      };
      const stock =
        item.kind === "consumable"
          ? { qty: (item.qty ?? 0) - form.qty }
          : { qtyOut: (item.qtyOut ?? 0) + form.qty };
      return mutate({
        label: item.name,
        refetch: ["items", "tx"],
        apply: (db) => ({
          ...patchItem(db, item.id, stock),
          tx: [optimistic, ...db.tx],
        }),
        request: () =>
          invApi.create("tx", {
            type: "checkout",
            itemId: item.id,
            user: form.user,
            email: form.email,
            qty: form.qty,
            expectedReturn: form.expectedReturn || null,
            condBefore: item.condition,
            purpose: form.purpose,
          }),
      });
    },

    returnLoan(item: InvItem, loan: InvTx, condAfter: string) {
      const qty = loan.qty ?? 1;
      const stock =
        item.kind === "consumable"
          ? { qty: (item.qty ?? 0) + qty }
          : { qtyOut: Math.max(0, (item.qtyOut ?? 0) - qty) };
      return mutate({
        label: item.name,
        refetch: ["items", "tx"],
        apply: (db) => ({
          ...patchItem(db, item.id, stock),
          tx: db.tx.map((t) =>
            t.id === loan.id ? { ...t, actualReturn: new Date().toISOString() } : t,
          ),
        }),
        request: () =>
          invApi.create("tx", {
            type: "return",
            itemId: item.id,
            qty,
            user: loan.user,
            email: loan.email,
            condAfter: condAfter || undefined,
            closesTxId: loan.id,
          }),
      });
    },

    adjust(item: InvItem, delta: number, note: string, actor: string) {
      return mutate({
        label: item.name,
        refetch: ["items", "tx"],
        apply: (db) => patchItem(db, item.id, { qty: (item.qty ?? 0) + delta }),
        request: () =>
          invApi.create("tx", {
            type: "adjust",
            itemId: item.id,
            qty: delta,
            user: actor,
            purpose: note,
          }),
      });
    },

    damage(item: InvItem, condAfter: string, note: string, actor: string, photo?: File | null) {
      return mutate({
        label: item.name,
        refetch: ["items", "tx"],
        apply: (db) => patchItem(db, item.id, { condition: condAfter || "Damaged" }),
        request: async () => {
          // Upload first so the transaction can reference the stored photo;
          // a failed upload fails the whole report (nothing half-recorded).
          const photoId = photo ? (await invApi.uploadPhoto(photo)).photoId : undefined;
          return invApi.create("tx", {
            type: "damage",
            itemId: item.id,
            condAfter: condAfter || "Damaged",
            user: actor,
            purpose: note,
            ...(photoId ? { photoId } : {}),
          });
        },
      });
    },

    createItem(fields: Partial<InvItem>) {
      const optimistic = { ...fields, id: fields.id || tempId() } as InvItem;
      return mutate({
        label: String(fields.name || "New item"),
        refetch: ["items"],
        apply: (db) => ({ ...db, items: [...db.items, optimistic] }),
        request: () => invApi.create("items", fields as Record<string, unknown>),
      });
    },

    editItem(item: InvItem, changes: Partial<InvItem>) {
      return mutate({
        label: item.name,
        refetch: ["items"],
        apply: (db) => patchItem(db, item.id, changes),
        request: () =>
          invApi.update("items", item.id, {
            ...changes,
            // Optimistic-concurrency precondition: a stale read 409s instead
            // of silently clobbering a concurrent edit.
            expectedUpdatedAt: item.updatedAt,
          }),
      });
    },

    archiveItem(item: InvItem) {
      return mutate({
        label: item.name,
        refetch: ["items"],
        apply: (db) => patchItem(db, item.id, { status: "Archived" }),
        request: () => invApi.action("items", item.id, "archive"),
      });
    },

    restoreItem(item: InvItem) {
      return mutate({
        label: item.name,
        refetch: ["items"],
        apply: (db) => patchItem(db, item.id, { status: "Available" }),
        request: () => invApi.action("items", item.id, "restore"),
      });
    },

    reserve(item: InvItem, fields: Partial<InvRes>) {
      const optimistic: InvRes = {
        id: tempId(),
        itemId: item.id,
        status: "Pending",
        ...fields,
      };
      return mutate({
        label: item.name,
        refetch: ["res", "items"], // Reserved is server-derived from the rows
        apply: (db) => ({ ...db, res: [...db.res, optimistic] }),
        request: () =>
          invApi.create("res", {
            itemId: item.id,
            status: "Pending",
            ...fields,
          }),
      });
    },

    setReservation(r: InvRes, itemName: string, status: "Approved" | "Denied") {
      return mutate({
        label: itemName,
        refetch: ["res", "items"], // Reserved is server-derived from the rows
        apply: (db) => ({
          ...db,
          res: db.res.map((x) => (x.id === r.id ? { ...x, status } : x)),
        }),
        request: () =>
          invApi.update("res", r.id, { status, expectedUpdatedAt: r.updatedAt }),
      });
    },

    cancelReservation(r: InvRes, itemName: string) {
      return mutate({
        label: itemName,
        refetch: ["res", "items"], // Reserved is server-derived from the rows
        apply: (db) => ({ ...db, res: db.res.filter((x) => x.id !== r.id) }),
        request: () => invApi.remove("res", r.id),
      });
    },

    saveUser(existing: InvUser | null, fields: Partial<InvUser>) {
      const label = String(fields.name || existing?.name || "Lab member");
      if (existing) {
        return mutate({
          label,
          refetch: ["users"],
          apply: (db) => ({
            ...db,
            users: db.users.map((u) => (u.id === existing.id ? { ...u, ...fields } : u)),
          }),
          request: () =>
            invApi.update("users", existing.id, {
              ...fields,
              expectedUpdatedAt: existing.updatedAt,
            }),
        });
      }
      const optimistic = { ...fields, id: tempId() } as InvUser;
      return mutate({
        label,
        refetch: ["users"],
        apply: (db) => ({ ...db, users: [...db.users, optimistic] }),
        request: () => invApi.create("users", fields as Record<string, unknown>),
      });
    },

    deleteUser(u: InvUser) {
      return mutate({
        label: u.name || "Lab member",
        refetch: ["users"],
        apply: (db) => ({ ...db, users: db.users.filter((x) => x.id !== u.id) }),
        request: () => invApi.remove("users", u.id),
      });
    },

    startPlaxis(fields: Partial<InvPlaxis>) {
      const optimistic: InvPlaxis = { id: tempId(), loggedOut: false, ...fields };
      return mutate({
        label: `PLAXIS seat ${(fields.seat ?? 0) + 1}`,
        refetch: ["plaxis"],
        apply: (db) => ({ ...db, plaxis: [...db.plaxis, optimistic] }),
        request: () => invApi.create("plaxis", { loggedOut: false, ...fields }),
      });
    },

    endPlaxis(sessionRow: InvPlaxis) {
      return mutate({
        label: `PLAXIS seat ${(sessionRow.seat ?? 0) + 1}`,
        refetch: ["plaxis"],
        apply: (db) => ({
          ...db,
          plaxis: db.plaxis.map((p) =>
            p.id === sessionRow.id ? { ...p, loggedOut: true } : p,
          ),
        }),
        request: () =>
          invApi.update("plaxis", sessionRow.id, {
            loggedOut: true,
            expectedUpdatedAt: sessionRow.updatedAt,
          }),
      });
    },
  };
}

export type InventoryActions = ReturnType<typeof makeActions>;
