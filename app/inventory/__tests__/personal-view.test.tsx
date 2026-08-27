/**
 * INVENTORY_PERSONAL_VIEW frontend: ownership identity (the client mirror of
 * the server's _owns_row), owner-gated Return / Cancel / Release controls
 * with the on-behalf labels for managers, the My Bench tab (first, default
 * landing, four sections with reassuring empty states), the "Mine only"
 * toggles (client-side, default on), and the layout guard the page learned
 * the hard way — jsdom does no layout, so the CSS rules are asserted from
 * the stylesheet itself. Flag OFF is pinned throughout: the existing suites
 * mock a store with no personal state, and this file re-asserts the old
 * renderings explicitly.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { InvDB, MyBench } from "../lib";
import {
  callerIdentity, conflictToastText, myItemIds, ownsRow, ownsRowByKey, seatsInUse,
} from "../lib";

const DB: InvDB = {
  items: [
    { id: "LL-FOS-001", name: "Fiber-optic interrogator", category: "Fiber optics",
      kind: "equipment", qty: 2, qtyOut: 2, status: "Borrowed", condition: "Good" },
    { id: "LL-GEO-002", name: "Geogrid roll", category: "Geosynthetics",
      kind: "equipment", qty: 1, qtyOut: 0, status: "Available", condition: "Good" },
  ],
  tx: [
    { id: "TX-J", itemId: "LL-FOS-001", type: "checkout", user: "Jiming Liu",
      email: "jiming@uvic.ca", qty: 1, ts: "2026-08-10T09:00:00", expectedReturn: "2026-09-10T09:00:00" },
    { id: "TX-S", itemId: "LL-FOS-001", type: "checkout", user: "Subash Koirala",
      email: "subash@uvic.ca", qty: 1, ts: "2026-08-12T09:00:00", expectedReturn: "2026-09-12T09:00:00" },
  ],
  // res/plaxis carry the owner KEY (email); `user` stays the display name
  // and is never an ownership input flag-on.
  res: [
    { id: "RS-J", itemId: "LL-GEO-002", user: "Jiming Liu", status: "Pending",
      email: "jiming@uvic.ca",
      start: "2026-09-01T09:00:00", end: "2026-09-02T09:00:00", updatedAt: "2026-08-20T00:00:00" },
    { id: "RS-S", itemId: "LL-GEO-002", user: "Subash Koirala", status: "Approved",
      email: "subash@uvic.ca",
      start: "2026-09-05T09:00:00", end: "2026-09-06T09:00:00", updatedAt: "2026-08-20T00:00:00" },
  ],
  plaxis: [
    { id: "PX-J", seat: 0, user: "Jiming Liu", loggedOut: false,
      email: "jiming@uvic.ca",
      start: "2026-08-26T09:00:00", end: "2026-08-26T12:00:00" },
    { id: "PX-S", seat: 1, user: "Subash Koirala", loggedOut: false,
      email: "subash@uvic.ca",
      start: "2026-08-26T09:00:00", end: "2026-08-26T12:00:00" },
  ],
  users: [
    { id: "U-1", name: "Jiming Liu", email: "jiming@uvic.ca", group: "Lin Lab" },
    { id: "U-2", name: "Subash Koirala", email: "subash@uvic.ca", group: "Lin Lab" },
    { id: "U-3", name: "Dr. Cheng Lin", email: "clin@uvic.ca", group: "Lin Lab" },
    { id: "U-4", name: "Shane Smith", email: "", group: "Lin Lab" },
  ],
  audit: [],
  alerts: [],
};

const ME: MyBench = {
  loans: [{ id: "TX-J", itemId: "LL-FOS-001", type: "checkout", user: "Jiming Liu",
    email: "jiming@uvic.ca", qty: 1, ts: "2026-08-10T09:00:00",
    expectedReturn: "2026-08-20T09:00:00", overdueDays: 6 }],
  reservations: [{ id: "RS-J", itemId: "LL-GEO-002", user: "Jiming Liu", status: "Pending",
    start: "2026-09-01T09:00:00", end: "2026-09-02T09:00:00" }],
  plaxis: [{ id: "PX-J", seat: 0, user: "Jiming Liu", loggedOut: false,
    start: "2026-08-26T09:00:00", end: "2026-08-26T12:00:00" }],
  alerts: [{ severity: "high", kind: "overdue", detail: "Jiming Liu has 1x interrogator overdue",
    itemId: "LL-FOS-001", refId: "TX-J", days: 6 }],
};

const EMPTY_ME: MyBench = { loans: [], reservations: [], plaxis: [], alerts: [] };

const JIMING = { id: "u1", email: "jiming@uvic.ca", full_name: "Jiming Liu", role: "user" };
const MANAGER = { id: "u3", email: "clin@uvic.ca", full_name: "Dr. Cheng Lin", role: "professor" };

// Mutable per-test store/session (the existing suites use static mocks; here
// the flag and the signed-in user vary between tests).
let mockStore: Record<string, unknown>;
let mockUser: Record<string, unknown>;
vi.mock("../use-inventory", () => ({ useInventory: () => mockStore }));
vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({ user: mockUser, loading: false, signOut: vi.fn() }),
}));

import { InventoryTab, SUBPAGES } from "../components/inventory-tab";
import ItemsPage from "../components/items";
import { CheckoutModal, PlaxisModal } from "../components/modals";
import PlaxisPage from "../components/plaxis";
import ReservationsPage from "../components/reservations";

const store = (personal: { enabled: boolean; me: MyBench | null }) => ({
  db: DB, state: "ready", loadError: "", load: vi.fn(), mutate: vi.fn(),
  photosEnabled: false, personal,
});

const actions = new Proxy({}, { get: () => vi.fn() }) as never;
const prefillOf = (u: { full_name: string; email: string }) => ({
  name: u.full_name, email: u.email, group: "", studentId: "",
});

afterEach(cleanup);

// --- pure ownership logic -----------------------------------------------------
describe("callerIdentity / ownsRow / myItemIds", () => {
  const id = callerIdentity(JIMING, DB.users);

  test("identity carries the email and both name spellings", () => {
    const roster = [{ id: "U-1", name: "J. Liu", email: "jiming@uvic.ca" }];
    const withRoster = callerIdentity(JIMING, roster);
    expect(withRoster.email).toBe("jiming@uvic.ca");
    expect(withRoster.names).toContain("jiming liu");
    expect(withRoster.names).toContain("j. liu");
  });

  test("ownsRow (loans): email match wins, name is the fallback", () => {
    expect(ownsRow({ email: "JIMING@uvic.ca", user: "someone else" }, id)).toBe(true);
    expect(ownsRow({ user: "Jiming Liu" }, id)).toBe(true); // legacy tx without email
    expect(ownsRow({ email: "subash@uvic.ca", user: "Subash Koirala" }, id)).toBe(false);
    expect(ownsRow({}, id)).toBe(false); // a row naming nobody is owned by nobody
  });

  test("ownsRowByKey (res/plaxis): the stored key ONLY — never the name", () => {
    expect(ownsRowByKey({ email: "JIMING@uvic.ca" }, id)).toBe(true);
    // A matching display name is NOT ownership: duplicate or corrected
    // names must not flip the result.
    expect(ownsRowByKey({ email: "impostor@uvic.ca", user: "Jiming Liu" } as never, id)).toBe(false);
    expect(ownsRowByKey({ user: "Jiming Liu" } as never, id)).toBe(false); // keyless
    expect(ownsRowByKey({}, id)).toBe(false);
  });

  test("myItemIds: loans by ownsRow, reservations by the key only", () => {
    const now = new Date("2026-08-26T12:00:00");
    const mine = myItemIds(DB.tx, DB.res, id, now);
    expect(mine).toEqual(new Set(["LL-FOS-001", "LL-GEO-002"]));
    const denied = myItemIds(DB.tx, [{ ...DB.res[0], status: "Denied" }], id, now);
    expect(denied.has("LL-GEO-002")).toBe(false);
    // A keyless reservation naming the caller does not count as theirs.
    const keyless = myItemIds([], [{ ...DB.res[0], email: undefined }], id, now);
    expect(keyless.size).toBe(0);
  });
});

// --- tab shell: My Bench first + default landing --------------------------------
describe("My Bench tab", () => {
  test("flag on: first in the order and the default landing page", () => {
    mockStore = store({ enabled: true, me: ME });
    mockUser = JIMING;
    render(<InventoryTab />);
    const tabs = within(screen.getByRole("tablist")).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent?.replace(/\d+$/, ""))).toEqual(["My Bench", ...SUBPAGES]);
    expect(within(screen.getByRole("tablist")).getByRole("tab", { selected: true }).textContent)
      .toContain("My Bench");
    // The four sections render from the /me payload.
    expect(screen.getByText(/checked out to you/i)).toBeTruthy();
    expect(screen.getByText(/your reservations/i)).toBeTruthy();
    expect(screen.getByText(/your plaxis seats/i)).toBeTruthy();
    expect(screen.getByText(/alerts for you/i)).toBeTruthy();
    expect(screen.getByText(/6d overdue/)).toBeTruthy(); // server-clock days
  });

  test("flag off: exactly the six pages, Dashboard lands first", () => {
    mockStore = store({ enabled: false, me: null });
    mockUser = JIMING;
    render(<InventoryTab />);
    const tabs = within(screen.getByRole("tablist")).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent?.replace(/\d+$/, ""))).toEqual([...SUBPAGES]);
    expect(within(screen.getByRole("tablist")).getByRole("tab", { selected: true }).textContent)
      .toContain("Dashboard");
  });

  test("empty states read as reassurance, not error", () => {
    mockStore = store({ enabled: true, me: EMPTY_ME });
    mockUser = JIMING;
    render(<InventoryTab />);
    expect(screen.getByText("No items currently checked out to you.")).toBeTruthy();
    expect(screen.getByText(/nothing is booked under your name/i)).toBeTruthy();
    expect(screen.getByText("You are not holding a PLAXIS seat.")).toBeTruthy();
    expect(screen.getByText("Nothing here needs your attention.")).toBeTruthy();
  });
});

// --- Return gating --------------------------------------------------------------
describe("Return (items drawer)", () => {
  const renderItems = (user: typeof JIMING, personalView: boolean, isManager = false) => {
    mockUser = user;
    return render(
      <ItemsPage
        db={DB}
        actions={actions}
        prefill={prefillOf(user)}
        isManager={isManager}
        selectedId="LL-FOS-001"
        onSelect={vi.fn()}
        personalView={personalView}
        identity={callerIdentity(user, DB.users)}
      />,
    );
  };

  test("flag on: a member is offered only their own loan", () => {
    renderItems(JIMING, true);
    fireEvent.click(screen.getByRole("button", { name: "Return" }));
    const options = within(screen.getByLabelText(/open loan/i)).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toContain("Jiming Liu");
    expect(screen.getByRole("button", { name: "Record return" })).toBeTruthy();
  });

  test("flag on: a manager sees every loan, labelled on-behalf", () => {
    renderItems(MANAGER, true, true);
    fireEvent.click(screen.getByRole("button", { name: "Return" }));
    const select = screen.getByLabelText(/open loan/i);
    expect(within(select).getAllByRole("option")).toHaveLength(2);
    // The first loan (Jiming's) is preselected — the confirm step names them.
    expect(screen.getByRole("button", { name: "Return for Jiming Liu" })).toBeTruthy();
    fireEvent.change(select, { target: { value: "TX-S" } });
    expect(screen.getByRole("button", { name: "Return for Subash Koirala" })).toBeTruthy();
  });

  test("flag off: every loan is offered to everyone, exactly as today", () => {
    renderItems(JIMING, false);
    fireEvent.click(screen.getByRole("button", { name: "Return" }));
    expect(within(screen.getByLabelText(/open loan/i)).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Record return" })).toBeTruthy();
  });
});

// --- Cancel gating + Mine only (reservations) -----------------------------------
describe("Reservations", () => {
  const renderRes = (user: typeof JIMING, personalView: boolean, isManager = false) => {
    mockUser = user;
    return render(
      <ReservationsPage
        db={DB}
        actions={actions}
        prefill={prefillOf(user)}
        isManager={isManager}
        onOpenItem={vi.fn()}
        personalView={personalView}
        identity={callerIdentity(user, DB.users)}
      />,
    );
  };

  test("flag on: Mine only defaults ON and filters to the caller's rows", () => {
    renderRes(JIMING, true);
    const toggle = screen.getByLabelText(/mine only/i) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    expect(screen.getAllByText("Jiming Liu").length).toBeGreaterThan(0);
    expect(screen.queryByText("Subash Koirala")).toBeNull();
    fireEvent.click(toggle); // off -> the whole lab again (full payload, client filter)
    expect(screen.getAllByText("Subash Koirala").length).toBeGreaterThan(0);
  });

  test("flag on: Cancel renders only on the caller's own rows — managers included", () => {
    renderRes(MANAGER, true, true);
    fireEvent.click(screen.getByLabelText(/mine only/i)); // manager owns none; show all
    // The manager still approves/denies, but cancels NOTHING of someone else's.
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Approve" }).length).toBeGreaterThan(0);
    cleanup();
    renderRes(JIMING, true);
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
  });

  test("flag off: no Mine only toggle, manager Cancel everywhere as today", () => {
    renderRes(MANAGER, false, true);
    expect(screen.queryByLabelText(/mine only/i)).toBeNull();
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(2);
  });

  test("flag on: Cancel follows the KEY, not the display name", () => {
    // Two rows both named "Jiming Liu" — one is really his, one carries an
    // impostor key; and one keyless legacy row also naming him. Only the
    // key-matching row offers Cancel (the name-collision case that used to
    // pass), and the keyless row belongs to nobody until backfilled.
    const db: InvDB = {
      ...DB,
      res: [
        { id: "RS-MINE", itemId: "LL-GEO-002", user: "Jiming Liu", status: "Pending",
          email: "jiming@uvic.ca", start: "2026-09-01T09:00:00", end: "2026-09-02T09:00:00" },
        { id: "RS-TWIN", itemId: "LL-GEO-002", user: "Jiming Liu", status: "Pending",
          email: "jiming2@uvic.ca", start: "2026-09-03T09:00:00", end: "2026-09-04T09:00:00" },
        { id: "RS-NOKEY", itemId: "LL-GEO-002", user: "Jiming Liu", status: "Pending",
          start: "2026-09-05T09:00:00", end: "2026-09-06T09:00:00" },
      ],
    };
    mockUser = JIMING;
    render(
      <ReservationsPage
        db={db}
        actions={actions}
        prefill={prefillOf(JIMING)}
        isManager={false}
        onOpenItem={vi.fn()}
        personalView
        identity={callerIdentity(JIMING, db.users)}
      />,
    );
    fireEvent.click(screen.getByLabelText(/mine only/i)); // show all three rows
    expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
    // And Mine only (back on) keeps only the key-matching row.
    fireEvent.click(screen.getByLabelText(/mine only/i));
    expect(screen.getAllByText("Jiming Liu").length).toBe(1);
  });
});

// --- Release gating (PLAXIS) ----------------------------------------------------
describe("PLAXIS release", () => {
  const renderPlx = (user: typeof JIMING, personalView: boolean, isManager = false) => {
    mockUser = user;
    return render(
      <PlaxisPage
        db={DB}
        actions={actions}
        prefill={prefillOf(user)}
        isManager={isManager}
        personalView={personalView}
        identity={callerIdentity(user, DB.users)}
      />,
    );
  };

  test("flag on: owner releases plainly; a manager's on-behalf is labelled", () => {
    renderPlx(JIMING, true);
    expect(screen.getAllByRole("button", { name: "Log out" })).toHaveLength(1);
    cleanup();
    renderPlx(MANAGER, true, true);
    expect(screen.getByRole("button", { name: "Log out for Jiming Liu" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Log out for Subash Koirala" })).toBeTruthy();
  });

  test("flag off: mine-or-manager affordance unchanged", () => {
    renderPlx(MANAGER, false, true);
    expect(screen.getAllByRole("button", { name: "Log out" })).toHaveLength(2);
  });

  test("flag on: a keyless seat shows Log out only to managers (on-behalf)", () => {
    const db: InvDB = {
      ...DB,
      plaxis: [{ id: "PX-NOKEY", seat: 0, user: "Jiming Liu", loggedOut: false,
        start: "2026-08-26T09:00:00", end: "2026-08-26T12:00:00" }],
    };
    mockUser = JIMING;
    render(
      <PlaxisPage db={db} actions={actions} prefill={prefillOf(JIMING)} isManager={false}
        personalView identity={callerIdentity(JIMING, db.users)} />,
    );
    // The named person is NOT the key owner (there is no key) — no button.
    expect(screen.queryByRole("button", { name: /log out/i })).toBeNull();
    cleanup();
    mockUser = MANAGER;
    render(
      <PlaxisPage db={db} actions={actions} prefill={prefillOf(MANAGER)} isManager
        personalView identity={callerIdentity(MANAGER, db.users)} />,
    );
    // A manager can still clear the legacy seat, labelled on-behalf.
    expect(screen.getByRole("button", { name: "Log out for Jiming Liu" })).toBeTruthy();
  });
});

// --- Mine only on the Inventory table -------------------------------------------
describe("Inventory table Mine only", () => {
  test("defaults ON, filters to items the caller has out or reserved", () => {
    mockUser = JIMING;
    render(
      <ItemsPage
        db={DB}
        actions={actions}
        prefill={prefillOf(JIMING)}
        isManager={false}
        selectedId={null}
        onSelect={vi.fn()}
        personalView
        identity={callerIdentity(JIMING, DB.users)}
      />,
    );
    const toggle = screen.getByLabelText(/mine only/i) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    // Jiming has LL-FOS-001 out and LL-GEO-002 reserved — both are his.
    expect(screen.getByText("Fiber-optic interrogator")).toBeTruthy();
    expect(screen.getByText("Geogrid roll")).toBeTruthy();
    cleanup();
    mockUser = MANAGER;
    render(
      <ItemsPage
        db={DB}
        actions={actions}
        prefill={prefillOf(MANAGER)}
        isManager
        selectedId={null}
        onSelect={vi.fn()}
        personalView
        identity={callerIdentity(MANAGER, DB.users)}
      />,
    );
    // The manager holds nothing: the empty state points at the toggle.
    expect(screen.getByText(/switch off “Mine only”/i)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/mine only/i));
    expect(screen.getByText("Fiber-optic interrogator")).toBeTruthy();
  });
});

// --- session 4: roster-gap note ---------------------------------------------
describe("People blank-email note", () => {
  test("flag on: an email-less roster row carries the cannot-be-named note", () => {
    mockStore = store({ enabled: true, me: EMPTY_ME });
    mockUser = MANAGER;
    render(<InventoryTab />);
    fireEvent.click(screen.getByRole("tab", { name: /^People & log/ }));
    // One note — only Shane Smith's row lacks an email. A note, not an
    // alert: it must NOT appear in the Needs Attention list.
    expect(screen.getAllByText(/No email — cannot be named on a checkout/)).toHaveLength(1);
    fireEvent.click(screen.getByRole("tab", { name: /^Dashboard/ }));
    expect(screen.queryByText(/cannot be named on a checkout/)).toBeNull();
  });

  test("flag off: the note does not render (the rule it describes is off)", () => {
    mockStore = store({ enabled: false, me: null });
    mockUser = MANAGER;
    render(<InventoryTab />);
    fireEvent.click(screen.getByRole("tab", { name: /^People & log/ }));
    expect(screen.queryByText(/cannot be named on a checkout/)).toBeNull();
  });
});

// --- session 3: seat counter, conflict toast, double-submit guard ---------------
describe("seatsInUse", () => {
  test("counts DISTINCT held seats, never session rows", () => {
    const dup = [
      { id: "a", seat: 0, loggedOut: false },
      { id: "b", seat: 0, loggedOut: false }, // the live Seat-1 duplicate case
      { id: "c", seat: 0, loggedOut: true },
    ];
    expect(seatsInUse(dup)).toBe(1);
    expect(seatsInUse([...dup, { id: "d", seat: 1, loggedOut: false }])).toBe(2);
    expect(seatsInUse([])).toBe(0);
  });
});

describe("conflictToastText", () => {
  test("flag-on surfaces the server detail; flag-off keeps the generic text", () => {
    const detail = "PLAXIS seat 1 is already committed for that window — conflicts with Dharun (…).";
    expect(conflictToastText(true, detail, "Seat 1")).toBe(detail);
    expect(conflictToastText(true, "", "Seat 1")).toMatch(/changed by someone else/);
    expect(conflictToastText(false, detail, "Seat 1")).toMatch(/changed by someone else/);
  });
});

describe("double-submit guard", () => {
  test("Book a seat fires exactly once on a double click", () => {
    const onSubmit = vi.fn();
    render(
      <PlaxisModal sessions={[]} prefill={prefillOf(JIMING)} onSubmit={onSubmit} onClose={vi.fn()} />,
    );
    const btn = screen.getByRole("button", { name: "Start session" });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  test("Check out fires exactly once on a double click", () => {
    const onSubmit = vi.fn();
    render(
      <CheckoutModal
        item={{ id: "LL-X", name: "Interrogator", kind: "equipment", qty: 2, qtyOut: 0 }}
        prefill={prefillOf(JIMING)}
        roster={[]}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/expected return/i), {
      target: { value: "2026-09-10" },
    });
    const btn = screen.getByRole("button", { name: "Check out" });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});

// --- layout guard (jsdom does no layout; assert the stylesheet) -----------------
describe("layout guard", () => {
  const css = readFileSync(join(process.cwd(), "app/inventory/inventory.module.css"), "utf8");
  const block = (sel: string) => css.slice(css.indexOf(sel), css.indexOf("}", css.indexOf(sel)));

  test("the tab strip opts out of flex shrinking, and the scroll column has an explicit min-height", () => {
    // A flex child that is also a scroll container collapses to zero height
    // with min-height: auto — the tablist vanished on tall pages once. My
    // Bench lives inside the same column, so both rules are re-asserted here.
    expect(block(".subnav {")).toMatch(/flex:\s*none/);
    expect(block(".pageHead {")).toMatch(/flex:\s*none/);
    const wrap = block(".wrap {");
    expect(wrap).toMatch(/min-height:\s*0/);
    expect(wrap).toMatch(/overflow-y:\s*auto/);
  });
});
