/**
 * Sidebar refresh contract, pinned at the component boundary with a mocked
 * fetch -- no browser, no network, no timers of our own.
 *
 * The contract: ThreadList re-fetches the history list ONLY on events --
 * mount, window focus, tab visibility, its own delete/rename flows, and
 * explicit fetchThreads() calls through the ref (how chat.tsx signals
 * thread create and first-message/format titles). Never on a timer: the
 * old 3-second poll must stay dead.
 */
import React, { createRef } from "react";
import {
  render,
  waitFor,
  fireEvent,
  act,
  cleanup,
} from "@testing-library/react";
import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";

import ThreadList from "../thread-list";

type ServerThread = {
  threadId: string;
  name: string;
  isGroup: boolean;
  createdAt: string;
  updatedAt: string;
};

let serverThreads: ServerThread[];
let historyGets: number;

const jsonResponse = (data: unknown) => ({
  ok: true,
  status: 200,
  json: async () => data,
});

beforeEach(() => {
  serverThreads = [
    {
      threadId: "t1",
      name: "First thread",
      isGroup: false,
      createdAt: "2026-08-01T10:00:00Z",
      updatedAt: "2026-08-01T10:00:00Z",
    },
    {
      threadId: "t2",
      name: "Second thread",
      isGroup: false,
      createdAt: "2026-08-02T10:00:00Z",
      updatedAt: "2026-08-02T10:00:00Z",
    },
  ];
  historyGets = 0;

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.includes("/threads/history") && method === "GET") {
        historyGets += 1;
        return jsonResponse({ threads: [...serverThreads] });
      }
      if (url.includes("/threads/history") && method === "PUT") {
        const body = JSON.parse(String(init?.body));
        serverThreads = serverThreads.map((t) =>
          t.threadId === body.threadId && body.newName
            ? { ...t, name: body.newName, updatedAt: new Date().toISOString() }
            : t,
        );
        return jsonResponse({ success: true });
      }
      if (url.includes("/threads/history") && method === "DELETE") {
        const body = JSON.parse(String(init?.body));
        serverThreads = serverThreads.filter(
          (t) => t.threadId !== body.threadId,
        );
        return jsonResponse({ success: true });
      }
      return jsonResponse({});
    }),
  );
});

// No vitest globals, so RTL's automatic cleanup never registers -- without
// this, earlier tests' mounted instances keep their focus/visibility
// listeners and every later event fans out to all of them.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const renderList = async () => {
  const ref = createRef<{ fetchThreads: () => Promise<void> }>();
  const onThreadSelect = vi.fn();
  const utils = render(
    <ThreadList
      ref={ref}
      currentThreadId={null as unknown as string}
      onThreadSelect={onThreadSelect}
    />,
  );
  await waitFor(() => expect(historyGets).toBeGreaterThan(0));
  await waitFor(() =>
    expect(utils.container.querySelectorAll('[class*="threadItem"]').length)
      .toBe(serverThreads.length),
  );
  return { ...utils, ref, onThreadSelect };
};

describe("ThreadList refresh contract", () => {
  test("mounts with one fetch and stays silent while idle (no timer)", async () => {
    await renderList();
    const afterMount = historyGets;
    // The removed poll fired every 3s; 3.5 idle seconds must produce zero
    // fetches. Real time on purpose -- an interval of any plausible period
    // would fire inside this window.
    await new Promise((r) => setTimeout(r, 3500));
    expect(historyGets).toBe(afterMount);
  }, 10000);

  test("refetches when the window regains focus", async () => {
    await renderList();
    const before = historyGets;
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(historyGets).toBe(before + 1));
  });

  test("refetches when the tab becomes visible again", async () => {
    await renderList();
    const before = historyGets;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(historyGets).toBe(before + 1));
  });

  test("fetchThreads via ref (thread create / title paths) refetches and renders the new row", async () => {
    const { ref, container } = await renderList();
    const before = historyGets;
    serverThreads.push({
      threadId: "t3",
      name: "Created elsewhere",
      isGroup: false,
      createdAt: "2026-08-05T10:00:00Z",
      updatedAt: "2026-08-05T10:00:00Z",
    });
    await act(async () => {
      await ref.current!.fetchThreads();
    });
    expect(historyGets).toBe(before + 1);
    await waitFor(() =>
      expect(
        container.querySelectorAll('[class*="threadItem"]').length,
      ).toBe(3),
    );
    expect(container.textContent).toContain("Created elsewhere");
  });

  test("renaming a thread PUTs, refetches, and shows the new name", async () => {
    const { container } = await renderList();
    const before = historyGets;

    const firstRow = container.querySelector('[class*="threadItem"]')!;
    fireEvent.click(firstRow.querySelector('[class*="editBtn"]')!);
    const input = container.querySelector(
      '[class*="threadNameInput"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Renamed by test" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(historyGets).toBe(before + 1));
    await waitFor(() =>
      expect(container.textContent).toContain("Renamed by test"),
    );
    const put = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(
      ([, init]) => init?.method === "PUT",
    );
    expect(put).toBeTruthy();
  });

  test("deleting a thread DELETEs, refetches, and removes the row", async () => {
    const { container } = await renderList();
    const before = historyGets;
    const rowsBefore = container.querySelectorAll(
      '[class*="threadItem"]',
    ).length;

    const firstRow = container.querySelector('[class*="threadItem"]')!;
    fireEvent.click(firstRow.querySelector('[class*="deleteBtn"]')!);

    await waitFor(() => expect(historyGets).toBe(before + 1));
    await waitFor(() =>
      expect(
        container.querySelectorAll('[class*="threadItem"]').length,
      ).toBe(rowsBefore - 1),
    );
  });

  test("stays silent after the operations too", async () => {
    const { container, ref } = await renderList();
    // Exercise one of each mutation, then hold still.
    serverThreads.push({
      threadId: "t9",
      name: "Ninth",
      isGroup: false,
      createdAt: "2026-08-05T11:00:00Z",
      updatedAt: "2026-08-05T11:00:00Z",
    });
    await act(async () => {
      await ref.current!.fetchThreads();
    });
    const firstRow = container.querySelector('[class*="threadItem"]')!;
    fireEvent.click(firstRow.querySelector('[class*="deleteBtn"]')!);
    await waitFor(() =>
      expect(
        container.querySelectorAll('[class*="threadItem"]').length,
      ).toBe(2),
    );

    const settled = historyGets;
    await new Promise((r) => setTimeout(r, 3500));
    expect(historyGets).toBe(settled);
  }, 10000);
});
