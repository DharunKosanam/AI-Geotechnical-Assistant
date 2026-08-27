/**
 * Phase 2 — mobile. jsdom does no layout, so these are STRUCTURAL
 * assertions: the CSS rules exist with the required values, and the DOM
 * carries the hooks the rules key on (card tables with data-labels, the
 * drawer's close affordance). Visual behaviour at 390/768px is the owner's
 * manual pass.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { InvDB } from "../lib";

const DB: InvDB = {
  items: [
    { id: "LL-FOS-001", name: "Fiber-optic interrogator (LUNA ODiSI)", category: "Fiber optics",
      kind: "equipment", qty: 1, qtyOut: 0, status: "Available", condition: "Good", location: "Small room" },
    { id: "LL-CON-005", name: "Clear packing tape", category: "Consumables", kind: "consumable",
      qty: 0, minStock: 4, status: "Depleted", supplier: "Staples" },
  ],
  tx: [], res: [], plaxis: [],
  users: [{ id: "U-001", name: "Dr. Cheng Lin", email: "clin@uvic.ca", group: "Lin Lab", role: "PI" }],
  audit: [{ id: "a1", ts: "2026-08-21T09:00:00", actor: "Subash Koirala", action: "create_items", entity: "inv_items:X" }],
  alerts: [],
};

vi.mock("../use-inventory", () => ({
  useInventory: () => ({ db: DB, state: "ready", loadError: "", load: vi.fn(), mutate: vi.fn() }),
}));
vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({
    user: { id: "u1", email: "clin@uvic.ca", full_name: "Dr. Cheng Lin", role: "professor" },
    loading: false, signOut: vi.fn(),
  }),
}));

import { InventoryTab } from "../components/inventory-tab";

afterEach(cleanup);

const css = readFileSync(join(process.cwd(), "app/inventory/inventory.module.css"), "utf8");
const mobileStart = css.indexOf("@media (max-width: 767px)");
const mobile = css.slice(mobileStart);

function rule(selector: string, block = mobile): string {
  const i = block.indexOf(selector);
  expect(i, `selector ${selector} missing`).toBeGreaterThan(-1);
  return block.slice(i, block.indexOf("}", i));
}

describe("mobile stylesheet (structural)", () => {
  test("a 767px block exists and the tablist stays flex: none (never collapsed)", () => {
    expect(mobileStart).toBeGreaterThan(-1);
    const subnav = css.slice(css.indexOf(".subnav {"), css.indexOf("}", css.indexOf(".subnav {")));
    expect(subnav).toMatch(/flex:\s*none/);
    expect(subnav).toMatch(/overflow-x:\s*auto/); // horizontally scrollable
    // The mobile block never re-declares flex on .subnav.
    expect(rule(".subnav {")).not.toMatch(/flex:/);
  });
  test("inputs are 16px and tap targets 44px on mobile", () => {
    expect(rule(".input, .select, .textarea")).toMatch(/font-size:\s*16px/);
    expect(rule(".btn, .btnPrimary, .btnDanger, .btnGhost")).toMatch(/min-height:\s*44px/);
    expect(rule(".subTab {")).toMatch(/min-height:\s*44px/);
  });
  test("tables become stacked cards led by name / status / available", () => {
    expect(rule(".cardTable thead")).toMatch(/display:\s*none/);
    expect(rule(".cardTable .cardLead {")).toMatch(/order:\s*-3/);
    expect(rule(".cardTable .cardStatus {")).toMatch(/order:\s*-2/);
    expect(rule(".cardTable .cardAvail")).toMatch(/order:\s*-1/);
    expect(rule(".cardTable td[data-label]::before")).toMatch(/attr\(data-label\)/);
  });
  test("drawer is a full-screen sheet and modals are full-screen", () => {
    expect(rule(".drawer {")).toMatch(/position:\s*fixed/);
    expect(rule(".drawer {")).toMatch(/inset:\s*0/);
    expect(rule(".modal {")).toMatch(/width:\s*100%/);
  });
  test("PLAXIS hour column is pinned while the grid scrolls sideways", () => {
    // Phase 4 pins the reservation calendar's item column the same way.
    expect(rule(".plxHour, .calItem {")).toMatch(/position:\s*sticky/);
    expect(rule(".plxHour, .calItem {")).toMatch(/left:\s*0/);
    // the wrapper scrolls horizontally at every width
    expect(rule(".plxGridWrap {", css)).toMatch(/overflow-x:\s*auto/);
  });
});

describe("filter row (structural)", () => {
  // Same class of bug as the tablist: jsdom does no layout, so assert the
  // rule. The search input needs a real flex-basis (so the row WRAPS instead
  // of squeezing it) and a readable minimum width.
  test("search input keeps a real basis and a 200px floor; the row wraps", () => {
    const search = rule(".searchInput {", css);
    expect(search).toMatch(/flex:\s*\d+\s+1\s+\d+px/);
    expect(search).toMatch(/min-width:\s*200px/);
    expect(search).not.toMatch(/flex:\s*1;/);
    expect(rule(".toolbar {", css)).toMatch(/flex-wrap:\s*wrap/);
    // Mobile: search spans its own full line and stays visible.
    expect(rule(".searchInput {")).toMatch(/flex-basis:\s*100%/);
  });
  test("other control rows cannot overflow on phones", () => {
    expect(rule(".plxNav {", css)).toMatch(/flex-wrap:\s*wrap/);
    expect(rule(".select { max-width", css)).toMatch(/max-width:\s*100%/);
  });
});

describe("export button labels name their content", () => {
  test("all five Reports exports", () => {
    render(<InventoryTab />);
    fireEvent.click(screen.getByRole("tab", { name: /^Reports/ }));
    for (const label of ["Export availability", "Export most borrowed", "Export overdue", "Export order list", "Export damaged"]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: /Export CSV/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Export inventory/ })).toBeNull();
  });
});

describe("mobile DOM hooks", () => {
  test("Inventory, Reports and People tables are card tables with data-labels", () => {
    render(<InventoryTab />);
    const pages: [string, RegExp][] = [
      ["Inventory", /Location/], ["Reports", /Supplier/], ["People & log", /Program/],
    ];
    for (const [page, label] of pages) {
      fireEvent.click(screen.getByRole("tab", { name: new RegExp(`^${page.replace(/[&]/g, "&")}`) }));
      const tables = document.querySelectorAll("table");
      expect(tables.length).toBeGreaterThan(0);
      const card = Array.from(tables).filter((t) => /cardTable/.test(t.className));
      expect(card.length, `${page}: no card table`).toBeGreaterThan(0);
      const labels = Array.from(document.querySelectorAll("td[data-label]")).map((td) => td.getAttribute("data-label"));
      expect(labels.some((l) => label.test(l || ""))).toBe(true);
      expect(document.querySelector("td[class*='cardLead']")).not.toBeNull();
    }
  });
  test("the item drawer has a close affordance (it covers the page on phones)", () => {
    render(<InventoryTab />);
    fireEvent.click(screen.getByRole("tab", { name: /^Inventory/ }));
    fireEvent.click(screen.getByText("Fiber-optic interrogator (LUNA ODiSI)"));
    const drawer = screen.getByRole("complementary", { name: /details for/i });
    fireEvent.click(within(drawer).getByRole("button", { name: /close details/i }));
    expect(screen.queryByRole("complementary", { name: /details for/i })).toBeNull();
  });
});

/**
 * People & Log overflow. The roster (7 columns: name, role, program, group,
 * email, since, actions) and the audit log must use the SAME overflow
 * handling as the Inventory table — a .tableWrap that scrolls sideways —
 * and never clip: buttons keep their label on one line and the toolbar
 * row wraps instead; card cells wrap unbreakable emails / JSON instead of
 * pushing the card past the panel. Structural guards, same style as the
 * tablist and search-width guards above.
 */
function block(selector: string, source: string): string {
  const at = source.indexOf(selector);
  if (at < 0) return "";
  return source.slice(at, source.indexOf("}", at) + 1);
}

describe("people & log overflow (structural)", () => {
  test("the shared .tableWrap scrolls sideways (one overflow pattern, not a third)", () => {
    expect(block(".tableWrap {", css)).toMatch(/overflow-x:\s*auto/);
  });
  test("buttons never break or shrink below their label; toolbars wrap instead", () => {
    const btn = block(".btn {", css);
    expect(btn).toMatch(/white-space:\s*nowrap/);
    expect(btn).toMatch(/flex:\s*none/);
    expect(block(".toolbar {", css)).toMatch(/flex-wrap:\s*wrap/);
  });
  test("action cells stay on one line; free-text cells wrap instead of widening the table", () => {
    expect(block(".actionsCell {", css)).toMatch(/white-space:\s*nowrap/);
    expect(block(".cellWrap {", css)).toMatch(/overflow-wrap:\s*anywhere/);
  });
  test("card cells cannot push a card past the panel on phones", () => {
    const td = block(".cardTable td {", mobile);
    expect(td).toMatch(/min-width:\s*0/);
    expect(td).toMatch(/max-width:\s*100%/);
    expect(td).toMatch(/overflow-wrap:\s*anywhere/);
  });
});

describe("people & log DOM hooks", () => {
  test("roster and audit tables sit inside .tableWrap as card tables led by name then role", () => {
    render(<InventoryTab />);
    fireEvent.click(screen.getByRole("tab", { name: /^People/ }));
    const tables = Array.from(document.querySelectorAll("table"));
    expect(tables.length).toBeGreaterThanOrEqual(2);
    for (const t of tables.slice(0, 2)) {
      expect(t.className).toMatch(/cardTable/);
      expect(t.parentElement?.className).toMatch(/tableWrap/);
    }
    // Roster row: name leads, role is the card's second slot, actions on their own line.
    const roster = tables[0];
    expect(roster.querySelector("td[class*='cardLead']")?.textContent).toContain("Dr. Cheng Lin");
    expect(roster.querySelector("td[class*='cardStatus']")?.textContent).toBe("PI");
    expect(roster.querySelector("td[class*='actionsCell']")?.textContent).toContain("Edit");
    // Audit detail cell carries the wrap class.
    expect(tables[1].querySelector("td[class*='cellWrap']")).not.toBeNull();
    // The header button is a real .btn (nowrap / flex:none apply to it).
    expect(screen.getByRole("button", { name: "Add member" }).className).toMatch(/btn/);
  });
});
