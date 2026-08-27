/**
 * Lab inventory — types, API client, and the pure logic the vitest suite
 * covers (optimistic mutation runner, role flags, CSV, PLAXIS grid math,
 * status mapping). No React in this file.
 *
 * Server truths this mirrors (python_backend/app/routers/inventory.py):
 *   GET  /api/inventory/{items|tx|res|plaxis|users}  -> { items, total }
 *   POST /api/inventory/{resource}                    -> created doc
 *   PUT  /api/inventory/{resource}/{id}               -> updated doc
 *        body may carry expectedUpdatedAt; stale -> 409 (conflict toast path)
 *   DELETE /api/inventory/{resource}/{id}             -> { deleted }
 *   GET  /api/inventory/alerts                        -> { alerts }
 *   GET  /api/inventory/status                        -> { enabled } (404 = flag off)
 * Transaction stock effects (qtyOut / consumable qty) are applied SERVER-side;
 * after every successful mutation the caller refetches, so derived fields
 * always show the server's value, never the optimistic one.
 */

import * as XLSX from "xlsx";

export type InvItem = {
  id: string;
  name: string;
  category?: string;
  subCategory?: string;
  kind?: "equipment" | "consumable" | "software" | string;
  manufacturer?: string;
  model?: string;
  serial?: string;
  qty?: number;
  qtyOut?: number;
  unit?: string;
  location?: string;
  custodian?: string;
  condition?: string;
  status?: string;
  minStock?: number;
  purchaseDate?: string | null;
  expiryDate?: string | null;
  maintDays?: number;
  lastMaint?: string | null;
  supplier?: string;
  notes?: string;
  description?: string;
  /** Server-derived (lastMaint + maintDays), stored so it is queryable.
   * Absent on rows written before it existed — render as "—". */
  nextMaint?: string | null;
  updatedAt?: string;
};

export type InvTx = {
  id: string;
  itemId: string;
  type: "checkout" | "return" | "adjust" | "damage" | string;
  user?: string;
  /** Owner key. Flag-on the server resolves it (roster email of the named
   * person, or the caller) — a client-typed value is never stored. */
  email?: string;
  /** Creator provenance: the JWT caller, server-stamped; visible flag-on. */
  createdByEmail?: string | null;
  group?: string;
  qty?: number;
  ts?: string | null;
  expectedReturn?: string | null;
  actualReturn?: string | null;
  condBefore?: string;
  condAfter?: string;
  purpose?: string;
  approval?: string;
  /** Damage reports only (INVENTORY_PHOTOS_ENABLED): a files-collection id. */
  photoId?: string | null;
  updatedAt?: string;
};

export type InvRes = {
  id: string;
  itemId: string;
  user?: string;
  /** Owner key (the same field inv_tx keys ownership on): server-stamped —
   * flag-on it is the roster email of the person the row NAMES (the caller
   * when it names them). Visible in responses only when
   * INVENTORY_PERSONAL_VIEW is on. `user` stays the display name. */
  email?: string | null;
  /** Creator provenance: the JWT caller, server-stamped; visible flag-on. */
  createdByEmail?: string | null;
  group?: string;
  start?: string | null;
  end?: string | null;
  purpose?: string;
  status?: "Pending" | "Approved" | "Denied" | string;
  notes?: string;
  qty?: number;
  updatedAt?: string;
};

export type InvPlaxis = {
  id: string;
  seat?: number;
  user?: string;
  /** Owner key — see InvRes.email. */
  email?: string | null;
  /** Creator provenance — see InvRes.createdByEmail. */
  createdByEmail?: string | null;
  group?: string;
  purpose?: string;
  start?: string | null;
  end?: string | null;
  loggedOut?: boolean;
  updatedAt?: string;
};

export type InvUser = {
  id: string;
  name?: string;
  email?: string;
  studentId?: string;
  role?: string;
  program?: string;
  group?: string;
  cosup?: string;
  since?: string;
  updatedAt?: string;
};

export type InvAudit = {
  id: string;
  ts?: string | null;
  actor?: string;
  action?: string;
  entity?: string;
  detail?: unknown;
  /** Whose record was changed, when it differs from who acted (an on-behalf
   * return/release, or a rejected attempt). Additive — older rows lack it. */
  owner?: string;
};

export type Alert = {
  severity: "high" | "medium" | "low" | string;
  kind: string;
  detail: string;
  /** Additive metadata from the server: deep-link target, the tx/res/plaxis
   * row the alert is about, and (overdue/expiry/maintenance) the day count
   * computed against the SERVER clock. */
  itemId?: string | null;
  refId?: string | null;
  days?: number | null;
};

export type InvDB = {
  items: InvItem[];
  tx: InvTx[];
  res: InvRes[];
  plaxis: InvPlaxis[];
  users: InvUser[];
  audit: InvAudit[];
  alerts: Alert[];
};

export const EMPTY_DB: InvDB = {
  items: [], tx: [], res: [], plaxis: [], users: [], audit: [], alerts: [],
};

// ---------------------------------------------------------------------------
// API client — same-origin relative paths, cookie-authenticated, r.ok checks
// with the backend's `detail` surfaced (the app-wide fetch convention).
// ---------------------------------------------------------------------------
const BASE = "/api/inventory";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${BASE}${path}`, { credentials: "include", ...init });
  } catch {
    throw new ApiError(0, "The inventory service could not be reached.");
  }
  if (!r.ok) {
    let detail = `Request failed (${r.status})`;
    try {
      const body = await r.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(r.status, detail);
  }
  return (await r.json()) as T;
}

export type Resource = "items" | "tx" | "res" | "plaxis" | "users" | "audit";

/** GET /api/inventory/me (INVENTORY_PERSONAL_VIEW): the caller's own rows.
 * The route is ABSENT (404) when the flag is off — the store treats that as
 * "personal view disabled", exactly like the /status probe treats its 404. */
export type MyBench = {
  loans: (InvTx & { overdueDays?: number })[];
  reservations: InvRes[];
  plaxis: InvPlaxis[];
  alerts: Alert[];
};

export const invApi = {
  status: () => call<{ enabled: boolean; photos?: boolean }>("/status"),
  me: () => call<MyBench>("/me"),
  /** Multipart upload (no content-type header: the browser sets the boundary). */
  uploadPhoto: (file: File) => {
    const body = new FormData();
    body.append("file", file, file.name);
    return call<{ photoId: string; url: string; mimetype: string; size: number }>("/photos", { method: "POST", body });
  },
  photoUrl: (photoId: string) => `${BASE}/photos/${encodeURIComponent(photoId)}`,
  list: <T>(resource: Resource, params = "") =>
    call<{ items: T[]; total: number }>(`/${resource}${params}`),
  create: <T>(resource: Resource, body: Record<string, unknown>) =>
    call<T>(`/${resource}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  update: <T>(resource: Resource, id: string, body: Record<string, unknown>) =>
    call<T>(`/${resource}/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  remove: (resource: Resource, id: string) =>
    call<{ deleted: string }>(`/${resource}/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** Named sub-actions (archive / restore) — POST, no body. */
  action: <T>(resource: Resource, id: string, verb: string) =>
    call<T>(`/${resource}/${encodeURIComponent(id)}/${verb}`, { method: "POST" }),
  alerts: () => call<{ alerts: Alert[] }>("/alerts"),
  backup: () => call<BackupFile>("/backup"),
  restore: (body: { backup: BackupFile; mode: RestoreMode; dryRun: boolean }) =>
    call<RestoreResult>("/restore", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
};

// --- Backup / restore -----------------------------------------------------------
export const BACKUP_SCHEMA_VERSION = 1;
export type BackupFile = {
  schemaVersion: number;
  exportedAt: string;
  collections: Partial<Record<"items" | "tx" | "res" | "plaxis" | "users" | "audit", unknown[]>>;
};
export type RestoreMode = "merge" | "replace";
export type CollectionDiff = {
  added: number; changed: number; removed: number; unchanged: number;
  addedIds: string[]; changedIds: string[]; removedIds: string[];
};
export type RestoreResult = { dryRun: boolean; mode: RestoreMode; diff: Record<string, CollectionDiff> };

/** Parse + validate a backup file BEFORE anything is sent: version and the
 * collections shape are checked here so a wrong file fails fast (the server
 * re-validates). Throws with a user-facing message. */
export function parseBackupFile(text: string): BackupFile {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("That file is not valid JSON.");
  }
  const b = data as Partial<BackupFile>;
  if (!b || typeof b !== "object") throw new Error("That file is not an inventory backup.");
  if (b.schemaVersion !== BACKUP_SCHEMA_VERSION) {
    throw new Error(`Unsupported backup schemaVersion ${String(b.schemaVersion)} (expected ${BACKUP_SCHEMA_VERSION}).`);
  }
  if (!b.collections || typeof b.collections !== "object") throw new Error("The backup has no collections.");
  const known = ["items", "tx", "res", "plaxis", "users", "audit"];
  for (const [k, v] of Object.entries(b.collections)) {
    if (!known.includes(k)) throw new Error(`Unknown collection in backup: ${k}`);
    if (!Array.isArray(v)) throw new Error(`Collection ${k} is not a list.`);
  }
  return b as BackupFile;
}

export function downloadText(filename: string, text: string, mime = "application/json"): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Optimistic mutation runner. Every write goes through this:
//   snapshot -> apply optimistically -> request -> reconcile (refetch; the
//   server is authoritative on derived fields) — or on ANY failure roll back
//   to the snapshot, run the 409 conflict hook (refetch current state; never
//   auto-retry), and surface the error.
// Pure I/O contract so the rollback and conflict paths are unit-testable.
// ---------------------------------------------------------------------------
export type MutationIO<S> = {
  getState: () => S;
  setState: (s: S) => void;
  apply: (s: S) => S;
  request: () => Promise<unknown>;
  reconcile: () => Promise<void>;
  onConflict?: (e: ApiError) => void | Promise<void>;
  onError?: (e: ApiError) => void;
};

/** The conflict-toast text. Flag-on the server's own 409 detail IS the
 * message — the overlap gates name the holder and window, and "changed by
 * someone else" would be wrong for them. Flag-off keeps today's generic
 * text (a control-audit finding: every 409 detail used to be swallowed). */
export function conflictToastText(personalOn: boolean, serverDetail: string, label: string): string {
  return personalOn && serverDetail
    ? serverDetail
    : `"${label}" was changed by someone else. The view has been refreshed — please retry.`;
}

export async function runMutation<S>(io: MutationIO<S>): Promise<boolean> {
  const snapshot = io.getState();
  io.setState(io.apply(snapshot));
  try {
    await io.request();
  } catch (e) {
    io.setState(snapshot); // roll back the optimistic apply
    const err = e instanceof ApiError ? e : new ApiError(0, String((e as Error)?.message || e));
    if (err.status === 409) await io.onConflict?.(err);
    io.onError?.(err);
    return false;
  }
  try {
    await io.reconcile();
  } catch {
    // Reconcile is best-effort: the write landed; the next full load trues up.
  }
  return true;
}

// ---------------------------------------------------------------------------
// Session-role flags. Presentation only — the server enforces the two
// manager-gated actions (delete items/users, reservation approval) with 403s.
// Backend roles are user / admin / professor; isManager = admin | professor.
// isPI was removed in the Phase-6 cleanup: it had no call sites.
// ---------------------------------------------------------------------------
export function roleFlags(role?: string | null): { isManager: boolean } {
  const r = (role || "").trim().toLowerCase();
  return { isManager: r === "professor" || r === "admin" };
}

// V-number: UVic student id, e.g. V00891234. Optional field; validated when
// non-empty.
export const V_NUMBER_RE = /^[Vv]\d{8}$/;

// ---------------------------------------------------------------------------
// CSV export (RFC 4180: CRLF rows, quote when a cell holds " , CR or LF).
// ---------------------------------------------------------------------------
export function toCSV(
  headers: string[],
  rows: (string | number | null | undefined)[][],
): string {
  const esc = (v: string | number | null | undefined) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [headers, ...rows].map((r) => r.map(esc).join(",")).join("\r\n") + "\r\n";
}

export function downloadCSV(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------
export function asDate(v?: string | null): Date | null {
  if (!v) return null;
  // A date-only string ("2026-04-02") would parse as UTC midnight and land
  // on the previous calendar day in any western timezone — treat it as a
  // LOCAL date so client date math matches the backend's naive-date math.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v.trim());
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(v?: string | null): string {
  const d = asDate(v);
  if (!d) return "—";
  return d.toLocaleDateString("en-CA"); // YYYY-MM-DD
}

export function fmtDateTime(v?: string | null): string {
  const d = asDate(v);
  if (!d) return "—";
  return `${d.toLocaleDateString("en-CA")} ${d.toTimeString().slice(0, 5)}`;
}

export function overdueDays(due?: string | null, now = new Date()): number {
  const d = asDate(due);
  if (!d || d >= now) return 0;
  return Math.floor((now.getTime() - d.getTime()) / 86_400_000);
}

// ---------------------------------------------------------------------------
// Status chips — eight statuses, each a fg/bg token pair in the CSS module.
// Chips always render their text label; color is never the only carrier.
// ---------------------------------------------------------------------------
export type StatusKey =
  | "available" | "borrowed" | "inuse" | "reserved"
  | "maintenance" | "missing" | "depleted" | "retired" | "archived";

export function statusKey(item: Pick<InvItem, "status" | "condition" | "kind" | "qty">): StatusKey {
  const s = (item.status || "").toLowerCase();
  const c = (item.condition || "").toLowerCase();
  if (s.includes("archiv")) return "archived";
  if (s.includes("missing") || c.includes("missing")) return "missing";
  if (s.includes("retir")) return "retired";
  if (s.includes("deplet") || (item.kind === "consumable" && (item.qty ?? 0) <= 0)) return "depleted";
  if (s.includes("mainten") || c.includes("calibration") || c.includes("damaged")) return "maintenance";
  if (s.includes("borrow")) return "borrowed";
  if (s.includes("in use")) return "inuse";
  if (s.includes("reserv")) return "reserved";
  return "available";
}

export const STATUS_LABEL: Record<StatusKey, string> = {
  available: "Available",
  borrowed: "Borrowed",
  inuse: "In use",
  reserved: "Reserved",
  maintenance: "Maintenance",
  missing: "Missing",
  depleted: "Depleted",
  retired: "Retired",
  archived: "Archived",
};

/** Items in circulation — archived ones are hidden from default views,
 * KPIs, reports and the reservation pickers (spec: archive, never delete). */
export function activeItems(items: InvItem[]): InvItem[] {
  return items.filter((i) => statusKey(i) !== "archived");
}
export function isArchived(item: Pick<InvItem, "status">): boolean {
  return (item.status || "").toLowerCase().includes("archiv");
}

// Strata rail — the 4px category bar on table rows. Category -> token-derived
// hue (set in the CSS module); anything unknown falls to the neutral stratum.
export function strataKey(category?: string): string {
  const c = (category || "").toLowerCase();
  if (c.includes("fiber") || c.includes("fibre")) return "fiber";
  if (c.includes("sensor")) return "sensor";
  if (c.includes("acquisition") || c.includes("daq")) return "daq";
  if (c.includes("geosynth")) return "geo";
  if (c.includes("apparatus")) return "apparatus";
  if (c.includes("consumable")) return "consumable";
  if (c.includes("material")) return "material";
  if (c.includes("software")) return "software";
  return "other";
}

// ---------------------------------------------------------------------------
// PLAXIS weekly grid math (2 concurrent seats).
// ---------------------------------------------------------------------------
export function weekStart(anchor: Date): Date {
  const d = new Date(anchor);
  d.setHours(0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7; // Monday = 0
  d.setDate(d.getDate() - dow);
  return d;
}

export function weekDays(anchor: Date): Date[] {
  const start = weekStart(anchor);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

/** Sessions overlapping the one-hour slot starting at day 00:00 + hour
 * (half-open, consistent with the backend's overlap rule). */
export function bookingsAt(sessions: InvPlaxis[], day: Date, hour: number): InvPlaxis[] {
  const slotStart = new Date(day);
  slotStart.setHours(hour, 0, 0, 0);
  const slotEnd = new Date(slotStart.getTime() + 3_600_000);
  return sessions.filter((s) => {
    const start = asDate(s.start);
    const end = asDate(s.end);
    if (!start || !end) return false;
    return start < slotEnd && end > slotStart;
  });
}

/** A seat held past its booked end without logging out. */
export function isStaleSeat(s: InvPlaxis, now = new Date()): boolean {
  if (s.loggedOut) return false;
  const end = asDate(s.end);
  return end !== null && end < now;
}

/** DISTINCT seats currently held — never session rows: two sessions on one
 * seat must read 1/2, not 2/2 (the dashboard once counted rows and showed a
 * full license while Seat 2 was free). */
export function seatsInUse(plaxis: InvPlaxis[]): number {
  return new Set(plaxis.filter((p) => !p.loggedOut).map((p) => p.seat ?? 0)).size;
}

/** Presentation-only guard for the 2-seat cap (the booking modal warns; the
 * server stores whatever a manager forces). */
export function seatConflicts(
  sessions: InvPlaxis[],
  seat: number,
  start: Date,
  end: Date,
): InvPlaxis[] {
  return sessions.filter((s) => {
    if (s.loggedOut || s.seat !== seat) return false;
    const sStart = asDate(s.start);
    const sEnd = asDate(s.end);
    if (!sStart || !sEnd) return false;
    return sStart < end && sEnd > start;
  });
}

// ---------------------------------------------------------------------------
// Roster join: session user (id/email/full_name/role from /auth/me carries no
// group or studentId) -> inv_users row by email, for modal prefill.
// ---------------------------------------------------------------------------
export type SessionPrefill = {
  name: string;
  email: string;
  group: string;
  studentId: string;
};

/** What the server will record for a borrower email: the roster row's
 * studentId · group, joined case-insensitively — the same rule the backend
 * applies at write time. Empty string when nothing resolves. */
export function rosterIdentityLine(email: string, roster: InvUser[]): string {
  const e = (email || "").trim().toLowerCase();
  if (!e) return "";
  const row = roster.find((u) => (u.email || "").trim().toLowerCase() === e);
  if (!row) return "";
  return [row.studentId, row.group].filter(Boolean).join(" · ");
}

// ---------------------------------------------------------------------------
// Ownership (INVENTORY_PERSONAL_VIEW) — the CLIENT mirror of the server's
// _owns_row rule, for control gating and the "Mine only" filters. Email match
// first (what the server records on transactions); the person's NAME — the
// JWT full_name plus the roster name joined by email — is the fallback, and
// the ONLY key for reservations and PLAXIS rows (no email column there).
// Presentation only: the server 403s regardless of what renders.
// ---------------------------------------------------------------------------
export type CallerIdentity = { email: string; names: string[] };

export function callerIdentity(
  session: { full_name?: string | null; email?: string | null } | null,
  roster: InvUser[],
): CallerIdentity {
  const email = (session?.email || "").trim().toLowerCase();
  const names = new Set<string>();
  const full = (session?.full_name || "").trim().toLowerCase();
  if (full) names.add(full);
  if (email) {
    const row = roster.find((u) => (u.email || "").trim().toLowerCase() === email);
    const rosterName = (row?.name || "").trim().toLowerCase();
    if (rosterName) names.add(rosterName);
  }
  return { email, names: [...names] };
}

export function ownsRow(
  row: { email?: string | null; user?: string | null },
  id: CallerIdentity,
): boolean {
  const rowEmail = (row.email || "").trim().toLowerCase();
  if (id.email && rowEmail && rowEmail === id.email) return true;
  const rowUser = (row.user || "").trim().toLowerCase();
  return Boolean(rowUser) && id.names.includes(rowUser);
}

/** Reservation / PLAXIS ownership: the stored owner KEY only — a display
 * name is not an ownership check (duplicate names, corrected spellings and
 * changed emails all silently flip it). A row with no key belongs to nobody
 * until the backfill resolves it. Mirrors the server's rule exactly; loans
 * keep ownsRow (tx rows have always carried the email key, name fallback
 * for legacy rows included). */
export function ownsRowByKey(
  row: { email?: string | null },
  id: CallerIdentity,
): boolean {
  const rowKey = (row.email || "").trim().toLowerCase();
  return Boolean(id.email) && Boolean(rowKey) && rowKey === id.email;
}

/** Item ids the caller currently has out or holds a live (non-denied, not
 * yet ended) reservation on — the "Mine only" filter for the Inventory
 * table. Client-side over the FULL payload; never a server-side filter.
 * Loans match by ownsRow (tx semantics); reservations by the owner KEY
 * only, like every other reservation ownership check. */
export function myItemIds(
  tx: InvTx[],
  res: InvRes[],
  id: CallerIdentity,
  now = new Date(),
): Set<string> {
  const out = new Set<string>();
  for (const t of openLoans(tx)) if (ownsRow(t, id)) out.add(t.itemId);
  for (const r of res) {
    if ((r.status || "").toLowerCase() === "denied") continue;
    const end = asDate(r.end);
    if (end && end < now) continue;
    if (ownsRowByKey(r, id)) out.add(r.itemId);
  }
  return out;
}

export function sessionPrefill(
  session: { full_name?: string | null; email?: string | null } | null,
  roster: InvUser[],
): SessionPrefill {
  const email = session?.email || "";
  const row = roster.find(
    (u) => (u.email || "").toLowerCase() === email.toLowerCase() && email,
  );
  return {
    name: session?.full_name?.trim() || row?.name || email,
    email,
    group: row?.group || "",
    studentId: row?.studentId || "",
  };
}

// ---------------------------------------------------------------------------
// Reports (prototype parity). Everything here is computed from data already
// in state — never an alternative to the server's alert list. Each report is
// a pure (headers, rows) builder so the CSV and the on-screen table can never
// drift, and the Export modal can show the exact row count before download.
// ---------------------------------------------------------------------------
export type Report = { title: string; headers: string[]; rows: (string | number | null | undefined)[][] };
export type ExportPayload = { title: string; csv: string; rows: number; report: Report };

export function buildExport(report: Report): ExportPayload {
  return { title: report.title, csv: toCSV(report.headers, report.rows), rows: report.rows.length, report };
}

/** Local calendar day, YYYY-MM-DD (en-CA locale = ISO order). */
export function dayKey(v?: string | Date | null): string {
  const d = v instanceof Date ? v : asDate(v);
  return d ? d.toLocaleDateString("en-CA") : "";
}

/** lastMaint + maintDays, or null when either is unset. */
export function nextMaint(item: Pick<InvItem, "lastMaint" | "maintDays">): Date | null {
  const last = asDate(item.lastMaint);
  const days = item.maintDays ?? 0;
  if (!last || !days || days <= 0) return null;
  return new Date(last.getTime() + days * 86_400_000);
}

export function availableQty(item: Pick<InvItem, "kind" | "qty" | "qtyOut">): number {
  if (item.kind === "consumable") return Math.max(0, item.qty ?? 0);
  return Math.max(0, (item.qty ?? 0) - (item.qtyOut ?? 0));
}

export function openLoans(tx: InvTx[]): InvTx[] {
  return tx.filter((t) => t.type === "checkout" && !t.actualReturn);
}

/** Checkout frequency per item, descending (ties broken by itemId so the
 * order is deterministic). */
export function checkoutFrequency(tx: InvTx[]): { itemId: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const t of tx) {
    if (t.type !== "checkout") continue;
    counts.set(t.itemId, (counts.get(t.itemId) ?? 0) + 1);
  }
  return Array.from(counts, ([itemId, count]) => ({ itemId, count })).sort(
    (a, b) => b.count - a.count || a.itemId.localeCompare(b.itemId),
  );
}

export type CategoryAvailability = {
  category: string;
  records: number;
  available: number;
  inUse: number;       // in use / borrowed / reserved
  maintenance: number; // maintenance (incl. damaged / needs calibration)
  missing: number;
};

/** Status roll-up per category, sorted by category name. Uses the same
 * statusKey mapping as the chips so the table agrees with the Inventory page. */
export function availabilityByCategory(items: InvItem[]): CategoryAvailability[] {
  const groups = new Map<string, CategoryAvailability>();
  for (const it of activeItems(items)) {
    const category = it.category || "Uncategorised";
    const g = groups.get(category) ?? {
      category, records: 0, available: 0, inUse: 0, maintenance: 0, missing: 0,
    };
    g.records += 1;
    const key = statusKey(it);
    if (key === "available") g.available += 1;
    else if (key === "inuse" || key === "borrowed" || key === "reserved") g.inUse += 1;
    else if (key === "maintenance") g.maintenance += 1;
    else if (key === "missing") g.missing += 1;
    groups.set(category, g);
  }
  return Array.from(groups.values()).sort((a, b) => a.category.localeCompare(b.category));
}

export type OverdueRow = {
  txId: string;
  itemId: string;
  item: string;
  user: string;
  email: string;
  group: string;
  qty: number;
  taken: string;
  due: string;
  daysOverdue: number;
};

/** Overdue open loans. "Days overdue" comes from the server's alert for that
 * loan (refId match — server clock) where one exists, so it agrees with the
 * Dashboard; the browser clock is only the fallback. */
export function overdueRows(tx: InvTx[], items: InvItem[], alerts: Alert[], now = new Date()): OverdueRow[] {
  const byId = new Map(items.map((i) => [i.id, i]));
  const serverDays = new Map<string, number>();
  for (const a of alerts) {
    if (a.kind === "overdue" && a.refId && typeof a.days === "number") serverDays.set(a.refId, a.days);
  }
  return openLoans(tx)
    .map((t) => {
      const days = serverDays.has(t.id) ? (serverDays.get(t.id) as number) : overdueDays(t.expectedReturn, now);
      return { t, days };
    })
    .filter(({ t, days }) => days > 0 || serverDays.has(t.id))
    .sort((a, b) => b.days - a.days || a.t.id.localeCompare(b.t.id))
    .map(({ t, days }) => ({
      txId: t.id,
      itemId: t.itemId,
      item: byId.get(t.itemId)?.name || t.itemId,
      user: t.user || "",
      email: t.email || "",
      group: t.group || "",
      qty: t.qty ?? 1,
      taken: dayKey(t.ts),
      due: dayKey(t.expectedReturn),
      daysOverdue: days,
    }));
}

/** Consumables at or below their minimum — the SERVER's rule (qty <= minStock
 * whenever minStock is numeric), so this list matches the low_stock alerts. */
export function lowStockItems(items: InvItem[]): InvItem[] {
  return activeItems(items).filter(
    (i) => i.kind === "consumable" && typeof i.minStock === "number" && (i.qty ?? 0) <= i.minStock,
  );
}

/** Damaged, missing, or in service (statusKey maintenance | missing). */
export function serviceItems(items: InvItem[]): InvItem[] {
  return activeItems(items).filter((i) => {
    const k = statusKey(i);
    return k === "maintenance" || k === "missing";
  });
}

// --- the five report builders --------------------------------------------------
export function inventoryReport(items: InvItem[]): Report {
  return {
    title: "Full inventory",
    headers: ["Item ID", "Name", "Type", "Category", "Sub-category", "Manufacturer", "Model", "Serial",
      "Qty", "Out", "Available", "Unit", "Location", "Condition", "Status", "Custodian", "Min stock",
      "Purchased", "Expiry", "Next service", "Notes"],
    rows: [...items]
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((i) => [
        i.id, i.name, i.kind, i.category, i.subCategory, i.manufacturer, i.model, i.serial,
        i.qty ?? 0, i.qtyOut ?? 0, availableQty(i), i.unit, i.location, i.condition, i.status,
        i.custodian, i.minStock ?? 0, dayKey(i.purchaseDate), dayKey(i.expiryDate),
        dayKey(nextMaint(i)), i.notes,
      ]),
  };
}

export function mostBorrowedReport(tx: InvTx[], items: InvItem[]): Report {
  const byId = new Map(items.map((i) => [i.id, i]));
  return {
    title: "Most borrowed",
    headers: ["Item", "Item ID", "Check-outs"],
    rows: checkoutFrequency(tx).map((f) => [byId.get(f.itemId)?.name || f.itemId, f.itemId, f.count]),
  };
}

export function overdueReport(tx: InvTx[], items: InvItem[], alerts: Alert[], now = new Date()): Report {
  return {
    title: "Overdue loans",
    headers: ["item", "user", "email", "group", "qty", "taken", "due", "days overdue"],
    rows: overdueRows(tx, items, alerts, now).map((r) => [
      r.item, r.user, r.email, r.group, r.qty, r.taken, r.due, r.daysOverdue,
    ]),
  };
}

export function lowStockReport(items: InvItem[]): Report {
  return {
    title: "Low stock",
    headers: ["Item", "On hand", "Unit", "Minimum", "Supplier", "Location", "Expiry"],
    rows: lowStockItems(items)
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((i) => [i.name, i.qty ?? 0, i.unit, i.minStock ?? 0, i.supplier, i.location, dayKey(i.expiryDate)]),
  };
}

export function serviceReport(items: InvItem[]): Report {
  return {
    title: "Maintenance and damage",
    headers: ["Item", "Status", "Condition", "Location", "Last service", "Next service", "Notes"],
    rows: serviceItems(items)
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((i) => [i.name, i.status, i.condition, i.location, dayKey(i.lastMaint), dayKey(nextMaint(i)), i.notes]),
  };
}

// ---------------------------------------------------------------------------
// Reservation overlap rule — the CLIENT mirror of the server gate
// (inventory_service.reservation_conflicts). Shown before submit; the server
// is the gate (409 names the holders). Quantity-aware, half-open windows.
// ---------------------------------------------------------------------------
export type ConflictHolder = {
  kind: "loan" | "reservation";
  id: string;
  user: string;
  qty: number;
  start: string;
  end: string;
};

export function reservationConflicts(
  item: Pick<InvItem, "id" | "kind" | "qty">,
  start: Date,
  end: Date,
  qty: number,
  tx: InvTx[],
  res: InvRes[],
  excludeId?: string | null,
): ConflictHolder[] {
  const capacity = item.qty ?? 0;
  const requested = Math.max(qty || 1, 0);
  const holders: ConflictHolder[] = [];
  let committed = 0;
  if (item.kind !== "consumable") {
    for (const t of openLoans(tx)) {
      if (t.itemId !== item.id) continue;
      const lStart = asDate(t.ts) ?? new Date(-8.64e15);
      const lEnd = asDate(t.expectedReturn);
      const overlaps = lEnd ? lStart < end && lEnd > start : lStart < end;
      if (!overlaps) continue;
      committed += t.qty ?? 1;
      holders.push({
        kind: "loan", id: t.id, user: t.user || "unknown", qty: t.qty ?? 1,
        start: fmtDateTime(t.ts), end: lEnd ? fmtDateTime(t.expectedReturn) : "open",
      });
    }
  }
  for (const r of res) {
    if (r.itemId !== item.id) continue;
    if (excludeId && r.id === excludeId) continue;
    if ((r.status || "").toLowerCase() === "denied") continue;
    const rStart = asDate(r.start);
    const rEnd = asDate(r.end);
    if (!rStart || !rEnd) continue;
    if (!(rStart < end && rEnd > start)) continue;
    committed += r.qty ?? 1;
    holders.push({
      kind: "reservation", id: r.id, user: r.user || "unknown", qty: r.qty ?? 1,
      start: fmtDateTime(r.start), end: fmtDateTime(r.end),
    });
  }
  return committed + requested <= capacity ? [] : holders;
}

export function conflictLine(holders: ConflictHolder[]): string {
  if (holders.length === 0) return "";
  return `Conflicts with ${holders.map((h) => `${h.user} (${h.start} → ${h.end})`).join("; ")}`;
}

// ---------------------------------------------------------------------------
// Inventory table: filters + column sorting (pure, so the page stays thin).
// ---------------------------------------------------------------------------
export type ItemFilters = {
  query?: string;
  category?: string;   // "all" | value
  status?: string;     // "all" | StatusKey
  location?: string;   // "all" | value
  condition?: string;  // "all" | value
};

export function filterItems(items: InvItem[], f: ItemFilters): InvItem[] {
  const q = (f.query || "").trim().toLowerCase();
  return items.filter((i) => {
    if (f.category && f.category !== "all" && (i.category || "Uncategorised") !== f.category) return false;
    if (f.status && f.status !== "all" && statusKey(i) !== f.status) return false;
    if (f.location && f.location !== "all" && (i.location || "—") !== f.location) return false;
    if (f.condition && f.condition !== "all" && (i.condition || "—") !== f.condition) return false;
    if (!q) return true;
    return [i.name, i.id, i.model, i.serial, i.location, i.custodian, i.notes, i.description]
      .some((v) => (v || "").toLowerCase().includes(q));
  });
}

export type ItemSortKey =
  | "name" | "category" | "qty" | "available" | "status" | "location" | "custodian";
export type SortDir = "asc" | "desc";

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

function sortValue(i: InvItem, key: ItemSortKey): string | number {
  switch (key) {
    case "qty": return i.qty ?? 0;
    case "available": return availableQty(i);
    case "status": return STATUS_LABEL[statusKey(i)];
    case "category": return i.category || "";
    case "location": return i.location || "";
    case "custodian": return i.custodian || "";
    default: return i.name || "";
  }
}

/** Stable sort by one column; ties fall back to item id so the order is
 * deterministic regardless of input order. */
export function sortItems(items: InvItem[], key: ItemSortKey, dir: SortDir): InvItem[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    const cmp = typeof va === "number" && typeof vb === "number"
      ? va - vb
      : collator.compare(String(va), String(vb));
    return (cmp || a.id.localeCompare(b.id)) * sign;
  });
}

/** Distinct, sorted values of a text field (for filter selects). */
export function distinctValues(items: InvItem[], key: "category" | "location" | "condition"): string[] {
  const fallback = key === "category" ? "Uncategorised" : "—";
  return Array.from(new Set(items.map((i) => i[key] || fallback))).sort(collator.compare);
}

// ---------------------------------------------------------------------------
// Reservation calendar — reuses the PLAXIS week math (weekStart/weekDays) and
// the same half-open overlap rule, at day granularity.
// ---------------------------------------------------------------------------
/** True when the reservation overlaps the calendar day [00:00, next 00:00). */
/**
 * Reservations page list. "" (the picker's "All items" default) shows every
 * reservation; an item id filters to that item. Newest window first.
 */
export function visibleReservations(res: InvRes[], itemId: string): InvRes[] {
  const rows = itemId ? res.filter((r) => r.itemId === itemId) : res;
  return [...rows].sort((a, b) => String(b.start || "").localeCompare(String(a.start || "")));
}

/**
 * The two empty states must not read alike: "none at all" vs "none for this
 * item while others exist" (the latter once masqueraded as the former).
 */
export function reservationsEmptyMessage(total: number, itemId: string, itemName: string): string {
  if (!itemId || total === 0) return "No reservations yet.";
  return `No reservations for ${itemName}. ${total} other${total === 1 ? "" : "s"} exist — choose "All items" to see them.`;
}

export function reservationCoversDay(r: Pick<InvRes, "start" | "end">, day: Date): boolean {
  const dayStart = new Date(day);
  dayStart.setHours(0, 0, 0, 0);
  const dayEnd = new Date(dayStart.getTime() + 86_400_000);
  const start = asDate(r.start);
  const end = asDate(r.end);
  if (!start || !end) return false;
  return start < dayEnd && end > dayStart;
}

export type CalendarRow = { itemId: string; name: string; cells: InvRes[][] };

/** Item rows × 7 day columns for the week containing ``anchor``. Only items
 * with a non-denied reservation touching the week appear; rows sorted by
 * name, spans within a cell by start. */
export function reservationCalendar(items: InvItem[], res: InvRes[], anchor: Date): { days: Date[]; rows: CalendarRow[] } {
  const days = weekDays(anchor);
  const byItem = new Map<string, InvRes[]>();
  for (const r of res) {
    if ((r.status || "").toLowerCase() === "denied") continue;
    if (!days.some((d) => reservationCoversDay(r, d))) continue;
    byItem.set(r.itemId, [...(byItem.get(r.itemId) ?? []), r]);
  }
  const rows: CalendarRow[] = Array.from(byItem, ([itemId, list]) => ({
    itemId,
    name: items.find((i) => i.id === itemId)?.name || itemId,
    cells: days.map((d) =>
      list.filter((r) => reservationCoversDay(r, d))
        .sort((a, b) => String(a.start || "").localeCompare(String(b.start || ""))),
    ),
  }));
  rows.sort((a, b) => a.name.localeCompare(b.name));
  return { days, rows };
}

// ---------------------------------------------------------------------------
// Excel export — fed from the SAME Report (headers + rows) the CSV uses, so
// the two formats cannot drift. SheetJS community build: column widths are
// honoured; cell styles (bold header) are NOT supported by the OSS build —
// reported in the rollout log rather than faked.
// ---------------------------------------------------------------------------
export function reportToAoa(report: Report): (string | number)[][] {
  return [report.headers, ...report.rows.map((r) => r.map((v) => (v === null || v === undefined ? "" : v)))];
}

export function buildWorkbook(report: Report): XLSX.WorkBook {
  const aoa = reportToAoa(report);
  const ws = XLSX.utils.aoa_to_sheet(aoa);
  // Auto-width: widest cell per column (capped so a long note can't blow up).
  ws["!cols"] = report.headers.map((_, c) => ({
    wch: Math.min(60, Math.max(8, ...aoa.map((row) => String(row[c] ?? "").length + 2))),
  }));
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, report.title.slice(0, 31) || "Report");
  return wb;
}

export function buildXlsx(report: Report): Uint8Array {
  return new Uint8Array(XLSX.write(buildWorkbook(report), { type: "array", bookType: "xlsx" }) as ArrayBuffer);
}

/** Rows read back out of an .xlsx buffer (first sheet), for tests/preview. */
export function readXlsxRows(data: Uint8Array): (string | number)[][] {
  const wb = XLSX.read(data, { type: "array" });
  const ws = wb.Sheets[wb.SheetNames[0]];
  return XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" }) as (string | number)[][];
}

export function downloadXlsx(filename: string, report: Report): void {
  const bytes = buildXlsx(report);
  // A fresh ArrayBuffer copy: TS 5.7+ types Uint8Array over ArrayBufferLike,
  // which Blob refuses; slicing the underlying buffer yields a plain ArrayBuffer.
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const blob = new Blob([buffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Damage photos — client-side pre-check mirrors the server rule (JPEG / PNG /
// WebP, 10 MB). The server sniffs the bytes; this only fails fast.
// ---------------------------------------------------------------------------
export const PHOTO_MAX_BYTES = 10 * 1024 * 1024;
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];

export function validateImageFile(file: Pick<File, "type" | "size" | "name">): string {
  if (file.size === 0) return "That file is empty.";
  if (file.size > PHOTO_MAX_BYTES) return `Photos are capped at ${PHOTO_MAX_BYTES / (1024 * 1024)} MB.`;
  if (!PHOTO_TYPES.includes(file.type)) return "Only JPEG, PNG or WebP images are accepted.";
  return "";
}
