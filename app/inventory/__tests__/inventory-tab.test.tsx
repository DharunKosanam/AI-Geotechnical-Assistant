/**
 * The six-page tablist must persist on EVERY sub-page (it vanished on the
 * tall pages once: a flex-column scroll container squeezed the overflow-x:auto
 * tablist to 0px). Two guards: the DOM one (tabs + badges render on all six
 * pages, clicking any tab works) and the layout one (jsdom has no layout, so
 * the CSS rule that prevents the collapse is asserted directly).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { InvDB } from "../lib";

const DB: InvDB = {
  items: [
    { id: "LL-FOS-001", name: "Fiber-optic interrogator (LUNA ODiSI)", category: "Fiber optics",
      kind: "equipment", qty: 1, qtyOut: 1, status: "Borrowed", condition: "Good" },
    { id: "LL-CON-005", name: "Clear packing tape", category: "Consumables", kind: "consumable",
      qty: 0, minStock: 4, status: "Depleted" },
  ],
  tx: [
    { id: "t1", itemId: "LL-FOS-001", type: "checkout", user: "Yongxuan Gao", email: "asaff@live.cn",
      qty: 1, ts: "2026-08-08T09:00:00", expectedReturn: "2026-08-18T09:00:00" },
  ],
  res: [
    { id: "r1", itemId: "LL-FOS-001", user: "Jiming Liu", start: "2026-09-01T09:00:00",
      end: "2026-09-02T09:00:00", status: "Pending" },
  ],
  plaxis: [],
  users: [{ id: "U-001", name: "Dr. Cheng Lin", email: "clin@uvic.ca", group: "Lin Lab" }],
  audit: [],
  alerts: [
    { severity: "high", kind: "overdue", detail: "Yongxuan Gao has 1x interrogator overdue",
      itemId: "LL-FOS-001", refId: "t1", days: 3 },
    { severity: "low", kind: "pending_approval", detail: "Jiming Liu awaits approval",
      itemId: "LL-FOS-001", refId: "r1" },
  ],
};

vi.mock("../use-inventory", () => ({
  useInventory: () => ({ db: DB, state: "ready", loadError: "", load: vi.fn(), mutate: vi.fn() }),
}));

vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({
    user: { id: "u1", email: "clin@uvic.ca", full_name: "Dr. Cheng Lin", role: "professor" },
    loading: false,
    signOut: vi.fn(),
  }),
}));

import { InventoryTab, SUBPAGES } from "../components/inventory-tab";

// A piece of content unique to each page, proving the page body rendered
// INSIDE the shell (and therefore under the persistent tablist).
const LANDMARK: Record<(typeof SUBPAGES)[number], RegExp> = {
  Dashboard: /needs attention/i,
  Inventory: /new item/i,
  Reservations: /new reservation/i,
  "PLAXIS seats": /book a seat/i,
  Reports: /availability by category/i,
  "People & log": /lab members/i,
};

afterEach(cleanup);

const tabNamed = (name: string) =>
  new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);

describe("InventoryTab shell", () => {
  test("the six-page tablist renders on every sub-page and keeps its badges", () => {
    render(<InventoryTab />);
    for (const page of SUBPAGES) {
      const tablist = screen.getByRole("tablist", { name: /inventory pages/i });
      const tabs = within(tablist).getAllByRole("tab");
      expect(tabs.map((t) => t.textContent?.replace(/\d+$/, ""))).toEqual([...SUBPAGES]);

      fireEvent.click(within(tablist).getByRole("tab", { name: tabNamed(page) }));

      // Still there after navigating — and selection moved.
      const after = screen.getByRole("tablist", { name: /inventory pages/i });
      expect(within(after).getAllByRole("tab")).toHaveLength(6);
      expect(within(after).getByRole("tab", { selected: true }).textContent).toContain(page);
      expect(screen.getAllByText(LANDMARK[page]).length).toBeGreaterThan(0);

      // Badges come from server data: 1 high alert on Dashboard, 1 pending on Reservations.
      expect(within(after).getByRole("tab", { name: /^Dashboard/ }).textContent).toBe("Dashboard1");
      expect(within(after).getByRole("tab", { name: /^Reservations/ }).textContent).toBe("Reservations1");
    }
  });

  test("no sub-page renders its own page heading — the shell owns it", () => {
    render(<InventoryTab />);
    for (const page of SUBPAGES) {
      fireEvent.click(screen.getByRole("tab", { name: tabNamed(page) }));
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    }
  });

  test("layout guard: the tablist cannot be squeezed to 0px by tall pages", () => {
    // jsdom does no layout, so assert the rule itself: .subnav must opt out
    // of flex shrinking (flex: none) inside the flex-column scroll container.
    const css = readFileSync(join(process.cwd(), "app/inventory/inventory.module.css"), "utf8");
    const block = css.slice(css.indexOf(".subnav {"), css.indexOf("}", css.indexOf(".subnav {")));
    expect(block).toMatch(/flex:\s*none/);
    const head = css.slice(css.indexOf(".pageHead {"), css.indexOf("}", css.indexOf(".pageHead {")));
    expect(head).toMatch(/flex:\s*none/);
  });
});
