/**
 * HIGHLIGHTS_ENABLED off (or no open thread) must be invisible: MessageList
 * renders id-bearing messages with no <mark>, makes no highlight requests and
 * logs nothing -- even for a thread that HAS highlights stored server-side.
 * The same messages WITH the layer render marks, so the negative is not vacuous.
 */
import React from "react";
import { render, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import MessageList from "../../message-list";
import { useThreadHighlights, type StoredHighlight } from "../use-thread-highlights";

const stored: StoredHighlight = {
  id: "h1", threadId: "t1", messageId: "m2", userId: "u1", startOffset: 6, endOffset: 22,
  selectedText: "bearing capacity", colour: "yellow", note: "", createdAt: "2026-08-17T00:00:00", updatedAt: "2026-08-17T00:00:00",
};
const messages = [
  { role: "user" as const, text: "q?", id: "m1" },
  { role: "assistant" as const, text: "The **bearing capacity** of soil", id: "m2", sources: [] },
];
const props = {
  containerRef: { current: null }, endRef: { current: null }, showWelcome: false, welcome: null,
  messages, showThinking: false, awaitingSince: null, canRetry: true, onRetry: () => {},
};

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ highlights: [stored] }) }));
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("flag off", () => {
  test("useThreadHighlights(enabled=false) never fetches and returns null", () => {
    const { result } = renderHook(() => useThreadHighlights("t1", false));
    expect(result.current).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  test("MessageList with the layer absent: no marks, no requests, no console output", () => {
    const warn = vi.spyOn(console, "warn");
    const error = vi.spyOn(console, "error");
    const { container } = render(<MessageList {...(props as any)} highlights={null} />);
    expect(container.querySelector("mark")).toBeNull();
    expect(container.querySelector("[data-hl-id]")).toBeNull();
    expect(container.textContent).toContain("The bearing capacity of soil");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
  });
});

describe("flag on (control)", () => {
  test("useThreadHighlights(enabled=true) fetches the thread's highlights once", async () => {
    const { result } = renderHook(() => useThreadHighlights("t1", true));
    await waitFor(() => expect(result.current?.byMessage.get("m2")?.length).toBe(1));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String(fetchSpy.mock.calls[0][0])).toBe("/api/assistants/threads/t1/highlights");
  });

  test("MessageList with the layer present draws the stored highlight", () => {
    const highlights = { byMessage: new Map([["m2", [stored]]]), actions: { create: vi.fn(), update: vi.fn(), remove: vi.fn() } };
    const { container } = render(<MessageList {...(props as any)} highlights={highlights} />);
    const marks = Array.from(container.querySelectorAll("mark[data-hl-id='h1']"));
    expect(marks.map((m) => m.textContent).join("")).toBe("bearing capacity");
  });
});
