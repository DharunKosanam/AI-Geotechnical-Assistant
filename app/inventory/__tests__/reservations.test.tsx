/**
 * Reservations list scoping. The list shows EVERY reservation by default
 * ("All items" is the picker's default); picking an item filters it; and
 * the two empty states read differently — "none at all" must never be
 * shown when reservations exist for other items (the LL-SEN-004 report:
 * the table said RESERVED while this page said "No reservations yet").
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { InvDB } from "../lib";

const DB: InvDB = {
  items: [
    { id: "LL-SEN-001", name: "TEROS 22 tensiometer", category: "Sensors", kind: "equipment",
      qty: 2, qtyOut: 0, status: "Available", condition: "Good" },
    { id: "LL-SEN-004", name: "EC5 soil moisture sensor (pigtail), 5 m", category: "Sensors",
      kind: "equipment", qty: 5, qtyOut: 0, status: "Reserved", condition: "Good" },
    { id: "LL-FOS-001", name: "Fiber-optic interrogator (LUNA ODiSI)", category: "Fiber optics",
      kind: "equipment", qty: 1, qtyOut: 0, status: "Available", condition: "Good" },
  ],
  tx: [], plaxis: [], audit: [], alerts: [],
  users: [{ id: "U-001", name: "Dr. Cheng Lin", email: "clin@uvic.ca", group: "Lin Lab" }],
  res: [
    { id: "r1", itemId: "LL-SEN-004", user: "Jiming Liu", start: "2026-09-01T09:00:00",
      end: "2026-09-02T09:00:00", status: "Approved", purpose: "Column test" },
    { id: "r2", itemId: "LL-FOS-001", user: "Yongxuan Gao", start: "2026-09-05T09:00:00",
      end: "2026-09-06T09:00:00", status: "Pending", purpose: "DFOS trial" },
  ],
};
let db: InvDB = DB;

vi.mock("../use-inventory", () => ({
  useInventory: () => ({ db, state: "ready", loadError: "", load: vi.fn(), mutate: vi.fn() }),
}));
vi.mock("../../lib/auth-context", () => ({
  useAuth: () => ({
    user: { id: "u1", email: "clin@uvic.ca", full_name: "Dr. Cheng Lin", role: "professor" },
    loading: false, signOut: vi.fn(),
  }),
}));

import { InventoryTab } from "../components/inventory-tab";

afterEach(() => { cleanup(); db = DB; });

function openReservations() {
  render(<InventoryTab />);
  fireEvent.click(screen.getByRole("tab", { name: /^Reservations/ }));
  return screen.getByRole("combobox", { name: "Item" }) as HTMLSelectElement;
}

describe("reservations list scoping", () => {
  test("defaults to All items and lists reservations for every item", () => {
    const picker = openReservations();
    expect(picker.value).toBe("");
    expect(picker.options[0].textContent).toBe("All items");
    expect(screen.getAllByText("Jiming Liu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Yongxuan Gao").length).toBeGreaterThan(0);
    expect(screen.queryByText(/No reservations/)).toBeNull();
    // Nothing to reserve until an item is chosen.
    expect((screen.getByRole("button", { name: "New reservation" }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("picking an item filters the list and enables New reservation", () => {
    const picker = openReservations();
    fireEvent.change(picker, { target: { value: "LL-SEN-004" } });
    expect(screen.getAllByText("Jiming Liu").length).toBeGreaterThan(0);
    // The queue still lists the other item's Pending request; the LIST does not.
    const table = screen.getByRole("columnheader", { name: "Purpose" }).closest("table")!;
    expect(table.textContent).toContain("Column test");
    expect(table.textContent).not.toContain("DFOS trial");
    expect((screen.getByRole("button", { name: "New reservation" }) as HTMLButtonElement).disabled).toBe(false);
  });

  test("empty for THIS item reads differently from empty overall", () => {
    const picker = openReservations();
    fireEvent.change(picker, { target: { value: "LL-SEN-001" } });
    const msg = screen.getByText(/No reservations for TEROS 22 tensiometer/);
    expect(msg.textContent).toContain("2 others exist");
    expect(msg.textContent).toContain("All items");
    expect(screen.queryByText("No reservations yet.")).toBeNull();
  });

  test("no reservations at all says so plainly", () => {
    db = { ...DB, res: [] };
    openReservations();
    expect(screen.getByText("No reservations yet.")).toBeTruthy();
    expect(screen.queryByText(/No reservations for/)).toBeNull();
  });
});
