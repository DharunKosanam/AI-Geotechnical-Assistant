/**
 * Highlights panel: ordering (message order, then offset), empty state,
 * two-step delete through the existing DELETE action, export buttons hitting
 * the export endpoint and triggering a download, and jump-to-mark flashing.
 */
import React from "react";
import { render, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import HighlightsPanel, { orderHighlights, jumpToHighlight } from "../highlights-panel";
import type { StoredHighlight } from "../use-thread-highlights";

const mk = (over: Partial<StoredHighlight>): StoredHighlight => ({
  id: "x", threadId: "t1", messageId: "m1", userId: "u", startOffset: 0, endOffset: 1,
  selectedText: "text", colour: "yellow", note: "", createdAt: "2026-08-17T10:00:00", updatedAt: "2026-08-17T10:00:00",
  ...over,
});
// createdAt order deliberately scrambled vs thread order.
const items = [
  mk({ id: "c", messageId: "m2", startOffset: 5, selectedText: "third (msg2)", createdAt: "2026-08-17T10:00:00" }),
  mk({ id: "b", messageId: "m1", startOffset: 40, selectedText: "second (msg1 @40)", note: "a note\nline 2", colour: "blue", createdAt: "2026-08-17T10:00:01" }),
  mk({ id: "a", messageId: "m1", startOffset: 3, selectedText: "first (msg1 @3)", colour: "green", createdAt: "2026-08-17T10:00:02" }),
  mk({ id: "z", messageId: "unknown", startOffset: 0, selectedText: "orphan sorts last", createdAt: "2026-08-17T09:00:00" }),
];
const messageIds = ["u1", "m1", "u2", "m2"];

let onDelete: ReturnType<typeof vi.fn>;
let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  onDelete = vi.fn(async () => true);
  fetchSpy = vi.fn(async () => ({
    ok: true, status: 200, headers: new Headers({ "content-disposition": 'attachment; filename="highlights_t_20260817.md"' }),
    blob: async () => new Blob(["# Highlights"], { type: "text/markdown" }),
  }));
  vi.stubGlobal("fetch", fetchSpy);
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  (URL as any).createObjectURL = vi.fn(() => "blob:x");
  (URL as any).revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const renderPanel = (over: Partial<React.ComponentProps<typeof HighlightsPanel>> = {}) =>
  render(<HighlightsPanel open threadId="t1" threadTitle="Bearing Q&A" items={items} messageIds={messageIds} onDelete={onDelete as unknown as (id: string) => Promise<boolean>} onClose={() => {}} {...over} />);

describe("ordering", () => {
  test("by message (thread order), then offset; unknown message last", () => {
    expect(orderHighlights(items, messageIds).map((h) => h.id)).toEqual(["a", "b", "c", "z"]);
  });
  test("renders rows in that order with swatch, text, note, date", () => {
    const { container } = renderPanel();
    const rows = Array.from(container.querySelectorAll("li"));
    expect(rows.map((r) => r.querySelector("span[class*=text]")!.textContent)).toEqual([
      "first (msg1 @3)", "second (msg1 @40)", "third (msg2)", "orphan sorts last",
    ]);
    expect(rows[1].textContent).toContain("a note\nline 2");
    expect(rows[1].querySelector("span[class*=swatch_blue]")).toBeTruthy();
    expect(rows[1].textContent).toMatch(/blue · /);
    expect(container.textContent).toContain("4 in Bearing Q&A");
  });
});

test("empty state", () => {
  const { container } = renderPanel({ items: [] });
  expect(container.querySelector("li")).toBeNull();
  expect(container.textContent).toContain("No highlights yet");
  expect(container.textContent).toContain("0 in Bearing Q&A");
});

test("closed panel renders nothing", () => {
  const { container } = renderPanel({ open: false });
  expect(container.innerHTML).toBe("");
});

test("delete is two-step and uses the provided remove action", async () => {
  const { getAllByLabelText, getByLabelText } = renderPanel();
  const btn = getAllByLabelText("Delete highlight")[0]; // row "a"
  fireEvent.click(btn);
  expect(onDelete).not.toHaveBeenCalled();
  expect(getByLabelText("Confirm delete highlight")).toBe(btn);
  fireEvent.click(btn);
  await waitFor(() => expect(onDelete).toHaveBeenCalledWith("a"));
  expect(onDelete).toHaveBeenCalledTimes(1);
});

test("export buttons fetch the export endpoint and trigger a download", async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const { getByTitle } = renderPanel();
  fireEvent.click(getByTitle("Download as Markdown"));
  await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
  expect(String(fetchSpy.mock.calls[0][0])).toBe("/api/assistants/threads/t1/highlights/export?format=md");
  expect((fetchSpy.mock.calls[0][1] as any).credentials).toBe("include");
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  fireEvent.click(getByTitle("Download as Excel"));
  await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
  expect(String(fetchSpy.mock.calls[1][0])).toBe("/api/assistants/threads/t1/highlights/export?format=xlsx");
});

test("clicking a row scrolls to its mark and flashes it", () => {
  const mark = document.createElement("mark");
  mark.setAttribute("data-hl-id", "b");
  mark.className = "hl hl-blue";
  document.body.appendChild(mark);
  const scroll = vi.fn();
  (mark as any).scrollIntoView = scroll;
  vi.useFakeTimers();
  try {
    expect(jumpToHighlight("b")).toBe(true);
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
    expect(mark.classList.contains("hlFlash")).toBe(true);
    act(() => { vi.advanceTimersByTime(1700); });
    expect(mark.classList.contains("hlFlash")).toBe(false);
    expect(jumpToHighlight("does-not-exist")).toBe(false);
  } finally {
    vi.useRealTimers();
    mark.remove();
  }
});
