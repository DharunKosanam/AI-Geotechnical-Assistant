import { describe, expect, test, vi } from "vitest";

import {
  ApiError,
  InvPlaxis,
  V_NUMBER_RE,
  bookingsAt,
  isStaleSeat,
  roleFlags,
  rosterIdentityLine,
  runMutation,
  seatConflicts,
  sessionPrefill,
  statusKey,
  toCSV,
} from "../lib";

// --- optimistic mutation runner ---------------------------------------------
type S = { value: number };

function harness(request: () => Promise<unknown>) {
  let state: S = { value: 1 };
  const reconcile = vi.fn(async () => undefined);
  const onConflict = vi.fn();
  const onError = vi.fn();
  const io = {
    getState: () => state,
    setState: (s: S) => { state = s; },
    apply: (s: S) => ({ value: s.value + 10 }),
    request,
    reconcile,
    onConflict,
    onError,
  };
  return { io, get: () => state, reconcile, onConflict, onError };
}

describe("runMutation", () => {
  test("success: optimistic apply sticks and reconcile runs", async () => {
    const request = vi.fn(async () => undefined);
    const h = harness(request);
    const ok = await runMutation(h.io);
    expect(ok).toBe(true);
    expect(h.get().value).toBe(11);
    expect(request).toHaveBeenCalledTimes(1);
    expect(h.reconcile).toHaveBeenCalledTimes(1);
    expect(h.onError).not.toHaveBeenCalled();
  });

  test("failure: state rolls back to the pre-mutation snapshot", async () => {
    const h = harness(vi.fn(async () => { throw new ApiError(500, "boom"); }));
    const ok = await runMutation(h.io);
    expect(ok).toBe(false);
    expect(h.get().value).toBe(1); // rolled back, not 11
    expect(h.reconcile).not.toHaveBeenCalled();
    expect(h.onConflict).not.toHaveBeenCalled();
    expect(h.onError).toHaveBeenCalledWith(expect.objectContaining({ status: 500 }));
  });

  test("409 conflict: rollback + conflict hook, and NO auto-retry", async () => {
    const request = vi.fn(async () => {
      throw new ApiError(409, "This record was changed by someone else.");
    });
    const h = harness(request);
    const ok = await runMutation(h.io);
    expect(ok).toBe(false);
    expect(h.get().value).toBe(1); // rolled back
    expect(h.onConflict).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledTimes(1); // never retried automatically
  });

  test("non-ApiError failures are wrapped and still roll back", async () => {
    const h = harness(vi.fn(async () => { throw new Error("network down"); }));
    await runMutation(h.io);
    expect(h.get().value).toBe(1);
    expect(h.onError).toHaveBeenCalledWith(expect.objectContaining({ status: 0 }));
  });
});

// --- session role derivation --------------------------------------------------
// Open-access cleanup: isManager now gates ONLY delete (items/users) and
// reservation approval; isPI was removed (no call sites). Everything else is
// open to any authenticated user — no role flag involved at all.
describe("roleFlags", () => {
  test("professor is a manager", () => {
    expect(roleFlags("professor")).toEqual({ isManager: true });
  });
  test("admin is a manager", () => {
    expect(roleFlags("admin")).toEqual({ isManager: true });
  });
  test("plain user is not", () => {
    expect(roleFlags("user")).toEqual({ isManager: false });
  });
  test("missing role is not", () => {
    expect(roleFlags(undefined)).toEqual({ isManager: false });
    expect(roleFlags(null)).toEqual({ isManager: false });
  });
  test("case-insensitive", () => {
    expect(roleFlags(" Professor ")).toEqual({ isManager: true });
  });
  test("no isPI flag remains", () => {
    expect("isPI" in roleFlags("professor")).toBe(false);
  });
});

// --- CSV ----------------------------------------------------------------------
describe("toCSV", () => {
  test("quotes commas, quotes and newlines; CRLF rows; trailing newline", () => {
    const csv = toCSV(
      ["a", "b"],
      [
        ["plain", 'say "hi"'],
        ["with,comma", "line\nbreak"],
        [null, undefined],
      ],
    );
    expect(csv).toBe(
      'a,b\r\nplain,"say ""hi"""\r\n"with,comma","line\nbreak"\r\n,\r\n',
    );
  });

  test("byte-stable for the same data", () => {
    const rows: (string | number)[][] = [["LL-FOS-001", "Fiber-optic interrogator (LUNA ODiSI)", 1]];
    expect(toCSV(["id", "name", "qty"], rows)).toBe(toCSV(["id", "name", "qty"], rows));
  });
});

// --- PLAXIS grid math ---------------------------------------------------------
const session = (over: Partial<InvPlaxis>): InvPlaxis => ({
  id: "p1",
  seat: 0,
  user: "Shane Smith",
  start: "2026-08-20T09:00:00",
  end: "2026-08-20T12:00:00",
  loggedOut: false,
  ...over,
});

describe("plaxis helpers", () => {
  const day = new Date(2026, 7, 20); // 2026-08-20 local

  test("bookingsAt slots a session into its hours only (half-open)", () => {
    const sessions = [session({})];
    expect(bookingsAt(sessions, day, 8)).toHaveLength(0);
    expect(bookingsAt(sessions, day, 9)).toHaveLength(1);
    expect(bookingsAt(sessions, day, 11)).toHaveLength(1);
    expect(bookingsAt(sessions, day, 12)).toHaveLength(0); // end is exclusive
  });

  test("isStaleSeat flags a held seat past its end, not logged-out ones", () => {
    const now = new Date(2026, 7, 20, 13, 0);
    expect(isStaleSeat(session({}), now)).toBe(true);
    expect(isStaleSeat(session({ loggedOut: true }), now)).toBe(false);
    expect(isStaleSeat(session({ end: "2026-08-20T14:00:00" }), now)).toBe(false);
  });

  test("seatConflicts respects seat number and ignores logged-out sessions", () => {
    const sessions = [session({}), session({ id: "p2", seat: 1, loggedOut: true })];
    const start = new Date(2026, 7, 20, 11, 0);
    const end = new Date(2026, 7, 20, 13, 0);
    expect(seatConflicts(sessions, 0, start, end)).toHaveLength(1);
    expect(seatConflicts(sessions, 1, start, end)).toHaveLength(0);
    // Back-to-back windows do not conflict (half-open).
    expect(
      seatConflicts(sessions, 0, new Date(2026, 7, 20, 12, 0), new Date(2026, 7, 20, 14, 0)),
    ).toHaveLength(0);
  });
});

// --- status mapping + prefill + V-number --------------------------------------
describe("statusKey", () => {
  test("maps the seeded vocabulary", () => {
    expect(statusKey({ status: "Available" })).toBe("available");
    expect(statusKey({ status: "Borrowed" })).toBe("borrowed");
    expect(statusKey({ status: "In use" })).toBe("inuse");
    expect(statusKey({ status: "Reserved" })).toBe("reserved");
    expect(statusKey({ status: "Under maintenance" })).toBe("maintenance");
    expect(statusKey({ status: "Depleted" })).toBe("depleted");
    expect(statusKey({ status: "Missing" })).toBe("missing");
    expect(statusKey({ status: "Retired" })).toBe("retired");
  });
  test("a damaged condition surfaces as maintenance; empty consumables read depleted", () => {
    expect(statusKey({ status: "Available", condition: "Damaged" })).toBe("maintenance");
    expect(statusKey({ status: "Available", kind: "consumable", qty: 0 })).toBe("depleted");
  });
});

describe("rosterIdentityLine", () => {
  const roster = [
    { id: "U-005", name: "Yongxuan Gao", email: "asaff@live.cn",
      group: "Lin Lab — DFOS", studentId: "V00555555" },
    { id: "U-016", name: "Cameron Schellenberg", email: "", group: "Lin Lab" },
  ];
  test("shows what the server will record for the typed borrower email", () => {
    expect(rosterIdentityLine("asaff@live.cn", roster)).toBe("V00555555 · Lin Lab — DFOS");
    expect(rosterIdentityLine("  ASAFF@LIVE.CN  ", roster)).toBe("V00555555 · Lin Lab — DFOS");
  });
  test("empty for unknown or blank email — and never matches blank roster emails", () => {
    expect(rosterIdentityLine("visitor@example.org", roster)).toBe("");
    expect(rosterIdentityLine("", roster)).toBe("");
    expect(rosterIdentityLine("   ", roster)).toBe("");
  });
});

describe("sessionPrefill", () => {
  const roster = [
    { id: "U-019", name: "Dharun Kosanam", email: "dharunk@uvic.ca", group: "Lin Lab", studentId: "V00891234" },
  ];
  test("joins the roster row by email for group and studentId", () => {
    const p = sessionPrefill({ full_name: "Dharun Kosanam", email: "dharunk@uvic.ca" }, roster);
    expect(p).toEqual({
      name: "Dharun Kosanam", email: "dharunk@uvic.ca", group: "Lin Lab", studentId: "V00891234",
    });
  });
  test("falls back cleanly when there is no roster match", () => {
    const p = sessionPrefill({ full_name: null, email: "new@uvic.ca" }, roster);
    expect(p).toEqual({ name: "new@uvic.ca", email: "new@uvic.ca", group: "", studentId: "" });
  });
});

describe("V_NUMBER_RE", () => {
  test.each(["V00891234", "v00891234"])("accepts %s", (v) => {
    expect(V_NUMBER_RE.test(v)).toBe(true);
  });
  test.each(["00891234", "V0089123", "V008912345", "V0089123X"])("rejects %s", (v) => {
    expect(V_NUMBER_RE.test(v)).toBe(false);
  });
});

// =============================================================================
// Prototype-parity additions: report builders, frequency, grouping, export
// =============================================================================
import {
  availabilityByCategory,
  buildExport,
  checkoutFrequency,
  inventoryReport,
  lowStockItems,
  lowStockReport,
  mostBorrowedReport,
  nextMaint,
  overdueReport,
  overdueRows,
  serviceItems,
  serviceReport,
  type Alert,
  type InvItem,
  type InvTx,
} from "../lib";

const ITEMS: InvItem[] = [
  { id: "LL-FOS-001", name: "Interrogator, LUNA \"ODiSI\"", category: "Fiber optics", kind: "equipment",
    qty: 1, qtyOut: 1, unit: "Nos", status: "Borrowed", condition: "Good", location: "Small room",
    maintDays: 180, lastMaint: "2026-04-02", notes: "Shared instrument.\nBook through Reservations" },
  { id: "LL-SEN-004", name: "EC5 sensor", category: "Sensors", kind: "equipment", qty: 5, qtyOut: 0,
    status: "Reserved", condition: "Good" },
  { id: "LL-EQP-001", name: "Rotating erosion apparatus", category: "Test apparatus", kind: "equipment",
    qty: 1, qtyOut: 0, status: "Under maintenance", condition: "Needs calibration", location: "Bay 1" },
  { id: "LL-CON-002", name: "Ziplock bags (1 L)", category: "Consumables", kind: "consumable", qty: 2,
    minStock: 5, unit: "packs", supplier: "ULINE", status: "Available" },
  { id: "LL-CON-003", name: "Paper towels", category: "Consumables", kind: "consumable", qty: 9,
    minStock: 6, unit: "rolls", status: "Available" },
  { id: "LL-GEO-001", name: "Geogrid rolls", category: "Geosynthetics", kind: "equipment", qty: 4,
    qtyOut: 0, status: "Available" },
  { id: "LL-SEN-099", name: "Lost probe", category: "Sensors", kind: "equipment", qty: 1, qtyOut: 0,
    status: "Missing" },
];

const TX: InvTx[] = [
  { id: "t1", itemId: "LL-FOS-001", type: "checkout", user: "Yongxuan Gao", email: "asaff@live.cn",
    group: "Lin Lab — DFOS", qty: 1, ts: "2026-08-08T09:00:00", expectedReturn: "2026-08-18T09:00:00" },
  { id: "t2", itemId: "LL-FOS-001", type: "checkout", user: "Jiming Liu", qty: 1,
    ts: "2026-07-01T09:00:00", expectedReturn: "2026-07-05T09:00:00", actualReturn: "2026-07-04T09:00:00" },
  { id: "t3", itemId: "LL-SEN-004", type: "checkout", user: "Saeed Mahjoubi", qty: 2,
    ts: "2026-08-10T09:00:00", expectedReturn: "2026-09-10T09:00:00" },
  { id: "t4", itemId: "LL-GEO-001", type: "checkout", user: "Jiming Liu", qty: 1,
    ts: "2026-08-01T09:00:00", expectedReturn: "2026-08-15T09:00:00" },
  { id: "t5", itemId: "LL-FOS-001", type: "return", user: "Jiming Liu", qty: 1, ts: "2026-07-04T09:00:00" },
];

const NOW = new Date(2026, 7, 21, 12, 0); // 2026-08-21 local

describe("checkoutFrequency", () => {
  test("counts checkouts only, descending, ties by itemId", () => {
    expect(checkoutFrequency(TX)).toEqual([
      { itemId: "LL-FOS-001", count: 2 }, // two checkouts (the return row is ignored)
      { itemId: "LL-GEO-001", count: 1 },
      { itemId: "LL-SEN-004", count: 1 },
    ]);
  });
  test("empty when nothing was ever checked out", () => {
    expect(checkoutFrequency([{ id: "x", itemId: "A", type: "adjust" }])).toEqual([]);
  });
});

describe("availabilityByCategory", () => {
  test("groups by category with the chip status mapping, sorted by name", () => {
    const rows = availabilityByCategory(ITEMS);
    expect(rows.map((r) => r.category)).toEqual([
      "Consumables", "Fiber optics", "Geosynthetics", "Sensors", "Test apparatus",
    ]);
    const sensors = rows.find((r) => r.category === "Sensors")!;
    expect(sensors).toEqual({ category: "Sensors", records: 2, available: 0, inUse: 1, maintenance: 0, missing: 1 });
    const apparatus = rows.find((r) => r.category === "Test apparatus")!;
    expect(apparatus.maintenance).toBe(1);
    const fiber = rows.find((r) => r.category === "Fiber optics")!;
    expect(fiber.inUse).toBe(1); // Borrowed counts as in use / out
  });
});

describe("overdueRows", () => {
  const serverAlerts: Alert[] = [
    // Server says 3 days (its clock); the browser math below would also give
    // 3 — so pin a deliberately different number to prove the server wins.
    { severity: "high", kind: "overdue", detail: "x", itemId: "LL-FOS-001", refId: "t1", days: 7 },
  ];
  test("prefers the server-clock day count from the alerts payload", () => {
    const rows = overdueRows(TX, ITEMS, serverAlerts, NOW);
    expect(rows.map((r) => r.txId)).toEqual(["t1", "t4"]);
    expect(rows[0].daysOverdue).toBe(7);            // from the server alert
    expect(rows[1].daysOverdue).toBe(6);            // browser fallback: due 08-15 -> 6 days
    expect(rows[1].item).toBe("Geogrid rolls");
  });
  test("excludes returned loans and loans not yet due", () => {
    const ids = overdueRows(TX, ITEMS, [], NOW).map((r) => r.txId);
    expect(ids).not.toContain("t2"); // returned
    expect(ids).not.toContain("t3"); // due next month
  });
  test("report columns are exactly item, user, email, group, qty, taken, due, days overdue", () => {
    const report = overdueReport(TX, ITEMS, serverAlerts, NOW);
    expect(report.headers).toEqual(["item", "user", "email", "group", "qty", "taken", "due", "days overdue"]);
    expect(report.rows[0]).toEqual([
      'Interrogator, LUNA "ODiSI"', "Yongxuan Gao", "asaff@live.cn", "Lin Lab — DFOS", 1,
      "2026-08-08", "2026-08-18", 7,
    ]);
  });
});

describe("low stock / service / next maintenance", () => {
  test("lowStockItems follows the server rule (qty <= minStock)", () => {
    expect(lowStockItems(ITEMS).map((i) => i.id)).toEqual(["LL-CON-002"]);
  });
  test("serviceItems = maintenance | missing", () => {
    expect(serviceItems(ITEMS).map((i) => i.id).sort()).toEqual(["LL-EQP-001", "LL-SEN-099"]);
  });
  test("nextMaint adds the interval to the last service date", () => {
    expect(nextMaint({ lastMaint: "2026-04-02", maintDays: 180 })?.toLocaleDateString("en-CA")).toBe("2026-09-29");
    expect(nextMaint({ lastMaint: "2026-04-02", maintDays: 0 })).toBeNull();
    expect(nextMaint({ lastMaint: null, maintDays: 90 })).toBeNull();
  });
});

describe("report CSV quoting + export row counts", () => {
  test("inventory report quotes the comma+quote name and the newline note", () => {
    const { csv, rows } = buildExport(inventoryReport(ITEMS));
    expect(rows).toBe(ITEMS.length);
    expect(csv).toContain('"Interrogator, LUNA ""ODiSI"""');
    expect(csv).toContain('"Shared instrument.\nBook through Reservations"');
    expect(csv.split("\r\n")[0]).toBe(
      "Item ID,Name,Type,Category,Sub-category,Manufacturer,Model,Serial,Qty,Out,Available,Unit,Location,Condition,Status,Custodian,Min stock,Purchased,Expiry,Next service,Notes",
    );
  });
  test("most borrowed report quotes the item name and carries counts", () => {
    const { csv, rows } = buildExport(mostBorrowedReport(TX, ITEMS));
    expect(rows).toBe(3);
    expect(csv.split("\r\n")[1]).toBe('"Interrogator, LUNA ""ODiSI""",LL-FOS-001,2');
  });
  test("overdue report quotes the name (needs it) and leaves the em-dash group plain", () => {
    const { csv } = buildExport(overdueReport(TX, ITEMS, [], NOW));
    // Sorted most-overdue first, so the geogrid (6 d) precedes the interrogator (3 d).
    const lines = csv.split("\r\n");
    expect(lines[1]).toBe("Geogrid rolls,Jiming Liu,,,1,2026-08-01,2026-08-15,6");
    expect(lines[2]).toBe('"Interrogator, LUNA ""ODiSI""",Yongxuan Gao,asaff@live.cn,Lin Lab — DFOS,1,2026-08-08,2026-08-18,3');
  });
  test("low stock report row count matches the filtered list", () => {
    const { csv, rows } = buildExport(lowStockReport(ITEMS));
    expect(rows).toBe(1);
    expect(csv.split("\r\n")[1]).toBe("Ziplock bags (1 L),2,packs,5,ULINE,,");
  });
  test("service report is byte-stable and counts rows", () => {
    const a = buildExport(serviceReport(ITEMS));
    const b = buildExport(serviceReport(ITEMS));
    expect(a.rows).toBe(2);
    expect(a.csv).toBe(b.csv);
  });
  test("an empty report still produces a header-only CSV with zero rows", () => {
    const { csv, rows } = buildExport(overdueReport([], ITEMS, [], NOW));
    expect(rows).toBe(0);
    expect(csv).toBe("item,user,email,group,qty,taken,due,days overdue\r\n");
  });
});

// =============================================================================
// Phase 1 — client mirror of the server overlap gate
// =============================================================================
import { conflictLine, reservationConflicts } from "../lib";

describe("reservationConflicts (client mirror)", () => {
  const item = { id: "LL-FOS-001", kind: "equipment", qty: 1 };
  const sensor = { id: "LL-SEN-004", kind: "equipment", qty: 3 };
  const d = (day: number, h = 9) => new Date(2026, 7, day, h, 0);
  const res = [
    { id: "r1", itemId: "LL-FOS-001", user: "Saeed Mahjoubi", start: "2026-08-23T09:00:00", end: "2026-08-24T09:00:00", status: "Approved", qty: 1 },
  ];
  test("abutting is allowed, overlap is named", () => {
    expect(reservationConflicts(item, d(24), d(25), 1, [], res)).toEqual([]);
    expect(reservationConflicts(item, d(22), d(23), 1, [], res)).toEqual([]);
    const holders = reservationConflicts(item, d(23, 12), d(25), 1, [], res);
    expect(holders.map((h) => h.user)).toEqual(["Saeed Mahjoubi"]);
    expect(conflictLine(holders)).toMatch(/^Conflicts with Saeed Mahjoubi \(/);
  });
  test("partial quantity is allowed up to capacity; denied never counts; self excluded", () => {
    const two = [{ id: "r2", itemId: "LL-SEN-004", user: "A", start: "2026-08-23T09:00:00", end: "2026-08-26T09:00:00", status: "Pending", qty: 2 }];
    expect(reservationConflicts(sensor, d(24), d(25), 1, [], two)).toEqual([]);
    expect(reservationConflicts(sensor, d(24), d(25), 2, [], two)).toHaveLength(1);
    const denied = [{ ...two[0], status: "Denied" }];
    expect(reservationConflicts(sensor, d(24), d(25), 3, [], denied)).toEqual([]);
    expect(reservationConflicts(item, d(23), d(24), 1, [], res, "r1")).toEqual([]);
  });
  test("open equipment loans commit the window (open-ended ones indefinitely)", () => {
    const loan = [{ id: "t1", itemId: "LL-FOS-001", type: "checkout", user: "Yongxuan Gao", qty: 1, ts: "2026-08-20T09:00:00", expectedReturn: null }];
    expect(reservationConflicts(item, d(30), d(31), 1, loan, [])).toHaveLength(1);
    const due = [{ ...loan[0], expectedReturn: "2026-08-23T09:00:00" }];
    expect(reservationConflicts(item, d(23), d(24), 1, due, [])).toEqual([]);
  });
});

// =============================================================================
// Phase 3 — filters, column sorting, item fields
// =============================================================================
import { distinctValues, filterItems, sortItems } from "../lib";

describe("filterItems / sortItems / distinctValues", () => {
  const items = [
    { id: "B", name: "beta", category: "Sensors", kind: "equipment", qty: 5, qtyOut: 2, status: "In use", location: "Small room", condition: "Good", custodian: "Zed" },
    { id: "A", name: "alpha", category: "Sensors", kind: "equipment", qty: 10, qtyOut: 0, status: "Available", location: "Storage", condition: "Fair", custodian: "Amy" },
    { id: "C", name: "Gamma", category: "Fiber optics", kind: "consumable", qty: 2, minStock: 3, status: "Available", location: "Small room", condition: "Good", custodian: "Bo", description: "spare cleaver blades" },
    { id: "D", name: "delta", category: "Fiber optics", kind: "equipment", qty: 1, qtyOut: 1, status: "Borrowed" },
  ];
  test("location and condition filters work alongside category/status/query", () => {
    expect(filterItems(items, { location: "Small room" }).map((i) => i.id)).toEqual(["B", "C"]);
    expect(filterItems(items, { condition: "Fair" }).map((i) => i.id)).toEqual(["A"]);
    expect(filterItems(items, { location: "Small room", category: "Fiber optics" }).map((i) => i.id)).toEqual(["C"]);
    expect(filterItems(items, { query: "cleaver" }).map((i) => i.id)).toEqual(["C"]); // description is searchable
    // rows with no location/condition show as "—" and are selectable as such
    expect(filterItems(items, { location: "—" }).map((i) => i.id)).toEqual(["D"]);
  });
  test("sorts by name (case-insensitive), numeric quantity/available, and status; desc flips", () => {
    expect(sortItems(items, "name", "asc").map((i) => i.id)).toEqual(["A", "B", "D", "C"]);
    expect(sortItems(items, "qty", "desc").map((i) => i.id)).toEqual(["A", "B", "C", "D"]);
    expect(sortItems(items, "available", "asc").map((i) => i.id)).toEqual(["D", "C", "B", "A"]); // 0,2,3,10
    expect(sortItems(items, "status", "asc").map((i) => i.id)).toEqual(["A", "C", "D", "B"]);   // Available×2 (tie→id), Borrowed, In use
    expect(sortItems(items, "location", "desc")[0].id).toBe("A"); // "Storage" > "Small room" > ""
  });
  test("sorting is deterministic on ties (item id) and never mutates the input", () => {
    const copy = [...items];
    sortItems(items, "category", "asc");
    expect(items).toEqual(copy);
    expect(sortItems(items, "category", "asc").map((i) => i.id)).toEqual(["C", "D", "A", "B"]);
  });
  test("distinctValues lists sorted unique values with a placeholder for missing", () => {
    expect(distinctValues(items, "location")).toEqual(["—", "Small room", "Storage"]);
    expect(distinctValues(items, "condition")).toEqual(["—", "Fair", "Good"]);
    expect(distinctValues(items, "category")).toEqual(["Fiber optics", "Sensors"]);
  });
});

// =============================================================================
// Phase 4 — reservation calendar (same week + half-open math as PLAXIS)
// =============================================================================
import { reservationCalendar, reservationCoversDay, weekDays } from "../lib";

describe("reservation calendar", () => {
  const r = { start: "2026-08-24T09:00:00", end: "2026-08-26T09:00:00" }; // Mon 09:00 → Wed 09:00
  test("covers the days it overlaps, half-open at midnight", () => {
    expect(reservationCoversDay(r, new Date(2026, 7, 23))).toBe(false); // Sun
    expect(reservationCoversDay(r, new Date(2026, 7, 24))).toBe(true);  // Mon
    expect(reservationCoversDay(r, new Date(2026, 7, 26))).toBe(true);  // Wed (ends 09:00 that day)
    expect(reservationCoversDay({ start: "2026-08-24T09:00:00", end: "2026-08-26T00:00:00" }, new Date(2026, 7, 26))).toBe(false);
    expect(reservationCoversDay(r, new Date(2026, 7, 27))).toBe(false);
  });
  test("builds item rows × the same 7 days weekDays() yields; denied excluded; spans sorted", () => {
    const items = [{ id: "B", name: "Beta" }, { id: "A", name: "Alpha" }];
    const res = [
      { id: "r1", itemId: "B", user: "Bo", start: "2026-08-25T13:00:00", end: "2026-08-25T15:00:00", status: "Approved" },
      { id: "r2", itemId: "B", user: "Al", start: "2026-08-25T09:00:00", end: "2026-08-25T11:00:00", status: "Pending" },
      { id: "r3", itemId: "A", user: "Cy", start: "2026-08-28T09:00:00", end: "2026-08-29T09:00:00", status: "Denied" },
      { id: "r4", itemId: "A", user: "Di", start: "2026-09-10T09:00:00", end: "2026-09-11T09:00:00", status: "Approved" }, // other week
    ];
    const anchor = new Date(2026, 7, 26);
    const cal = reservationCalendar(items, res, anchor);
    expect(cal.days.map((d) => d.getTime())).toEqual(weekDays(anchor).map((d) => d.getTime()));
    expect(cal.rows.map((x) => x.name)).toEqual(["Beta"]); // Alpha only has denied / other-week rows
    const tuesday = cal.rows[0].cells[1];
    expect(tuesday.map((x) => x.id)).toEqual(["r2", "r1"]); // sorted by start
    expect(cal.rows[0].cells[0]).toEqual([]);
  });
});

// =============================================================================
// Phase 5 — archived items are out of circulation
// =============================================================================
import { activeItems, availabilityByCategory as byCat, isArchived } from "../lib";

describe("archived items", () => {
  const items = [
    { id: "A", name: "live", category: "Sensors", kind: "equipment", qty: 1, status: "Available" },
    { id: "Z", name: "old", category: "Sensors", kind: "consumable", qty: 0, minStock: 2, status: "Archived", condition: "Damaged" },
  ];
  test("map to their own status key and are filtered from active views/reports", () => {
    expect(statusKey({ status: "Archived" })).toBe("archived");
    expect(isArchived({ status: "Archived" })).toBe(true);
    expect(activeItems(items).map((i) => i.id)).toEqual(["A"]);
    expect(byCat(items)[0]).toEqual({ category: "Sensors", records: 1, available: 1, inUse: 0, maintenance: 0, missing: 0 });
    expect(lowStockItems(items)).toEqual([]);
    expect(serviceItems(items)).toEqual([]);
  });
});

// =============================================================================
// Phase 6 — backup file validation happens client-side before any request
// =============================================================================
import { parseBackupFile } from "../lib";

describe("parseBackupFile", () => {
  test("accepts a v1 backup and returns it typed", () => {
    const b = parseBackupFile(JSON.stringify({ schemaVersion: 1, exportedAt: "2026-08-21T12:00:00", collections: { items: [] } }));
    expect(b.schemaVersion).toBe(1);
  });
  test.each([
    ["not json", /valid JSON/],
    [JSON.stringify({ schemaVersion: 2, collections: {} }), /schemaVersion 2/],
    [JSON.stringify({ schemaVersion: 1 }), /no collections/],
    [JSON.stringify({ schemaVersion: 1, collections: { secrets: [] } }), /Unknown collection/],
    [JSON.stringify({ schemaVersion: 1, collections: { items: {} } }), /not a list/],
  ])("rejects bad input: %s", (text, msg) => {
    expect(() => parseBackupFile(text)).toThrow(msg);
  });
});

// =============================================================================
// Phase 7 — XLSX and CSV come from one builder and carry identical rows
// =============================================================================
import { buildXlsx, readXlsxRows, reportToAoa } from "../lib";

describe("xlsx export", () => {
  const reports = [
    inventoryReport(ITEMS),
    mostBorrowedReport(TX, ITEMS),
    overdueReport(TX, ITEMS, [], NOW),
    lowStockReport(ITEMS),
    serviceReport(ITEMS),
  ];
  test.each(reports.map((r) => [r.title, r] as const))("%s: workbook rows == report rows == CSV rows", (_t, report) => {
    const back = readXlsxRows(buildXlsx(report));
    const expected = reportToAoa(report);
    expect(back).toEqual(expected);
    // CSV row count (header + data rows) matches the sheet, and the header
    // line is the same header row.
    const csv = toCSV(report.headers, report.rows);
    expect(csv.split("\r\n").filter(Boolean).length).toBe(back.length);
    expect(back[0]).toEqual(report.headers);
  });
  test("cells with commas, quotes and newlines survive the round trip intact", () => {
    const back = readXlsxRows(buildXlsx(inventoryReport(ITEMS)));
    const row = back.find((r) => r[0] === "LL-FOS-001")!;
    expect(row[1]).toBe('Interrogator, LUNA "ODiSI"');
    expect(row[20]).toBe("Shared instrument.\nBook through Reservations");
  });
});

// =============================================================================
// Phase 8 — photo pre-check mirrors the server rule
// =============================================================================
import { validateImageFile } from "../lib";

describe("validateImageFile", () => {
  test("accepts jpeg/png/webp under 10 MB; rejects others by type or size", () => {
    expect(validateImageFile({ name: "a.jpg", type: "image/jpeg", size: 1024 })).toBe("");
    expect(validateImageFile({ name: "a.webp", type: "image/webp", size: 1024 })).toBe("");
    expect(validateImageFile({ name: "a.gif", type: "image/gif", size: 1024 })).toMatch(/JPEG, PNG or WebP/);
    expect(validateImageFile({ name: "a.png", type: "image/png", size: 11 * 1024 * 1024 })).toMatch(/10 MB/);
    expect(validateImageFile({ name: "a.png", type: "image/png", size: 0 })).toMatch(/empty/);
  });
});

import { reservationsEmptyMessage, visibleReservations } from "../lib";

describe("reservations list scoping (pure)", () => {
  const res = [
    { id: "a", itemId: "X", start: "2026-09-01T09:00:00" },
    { id: "b", itemId: "Y", start: "2026-09-05T09:00:00" },
    { id: "c", itemId: "X", start: "2026-09-03T09:00:00" },
  ];
  test("'' shows everything newest-first; an id filters", () => {
    expect(visibleReservations(res, "").map((r) => r.id)).toEqual(["b", "c", "a"]);
    expect(visibleReservations(res, "X").map((r) => r.id)).toEqual(["c", "a"]);
    expect(visibleReservations(res, "Z")).toEqual([]);
  });
  test("the two empty states differ", () => {
    expect(reservationsEmptyMessage(0, "", "")).toBe("No reservations yet.");
    expect(reservationsEmptyMessage(0, "X", "Thing")).toBe("No reservations yet.");
    expect(reservationsEmptyMessage(3, "", "")).toBe("No reservations yet.");
    expect(reservationsEmptyMessage(1, "X", "Thing")).toBe(
      'No reservations for Thing. 1 other exist — choose "All items" to see them.',
    );
    expect(reservationsEmptyMessage(2, "X", "Thing")).toContain("2 others exist");
  });
});
