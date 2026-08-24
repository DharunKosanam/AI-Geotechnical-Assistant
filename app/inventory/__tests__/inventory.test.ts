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
