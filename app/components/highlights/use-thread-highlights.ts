"use client";

/**
 * Thread-level highlight state: the list for the open thread (fetched when
 * the thread changes) plus create/update/delete against the flag-gated
 * FastAPI routes. Returns null when the feature is off or no thread is open,
 * so callers can pass "nothing" down and render exactly as before.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { API_ENDPOINTS } from "../../config/api";

export type StoredHighlight = {
  id: string;
  threadId: string;
  messageId: string;
  userId: string;
  startOffset: number;
  endOffset: number;
  selectedText: string;
  colour: string;
  note: string;
  createdAt: string;
  updatedAt: string;
};

export type HighlightCreateInput = {
  messageId: string;
  startOffset: number;
  endOffset: number;
  selectedText: string;
  colour: string;
  note?: string;
};

export type HighlightActions = {
  create: (input: HighlightCreateInput) => Promise<StoredHighlight | null>;
  update: (id: string, patch: { colour?: string; note?: string }) => Promise<StoredHighlight | null>;
  remove: (id: string) => Promise<boolean>;
};

export type ThreadHighlights = {
  /** Every highlight in the thread, in server (createdAt) order. */
  items: StoredHighlight[];
  byMessage: Map<string, StoredHighlight[]>;
  actions: HighlightActions;
};

const EMPTY: StoredHighlight[] = [];

export function useThreadHighlights(threadId: string | null, enabled: boolean): ThreadHighlights | null {
  const [items, setItems] = useState<StoredHighlight[]>(EMPTY);

  useEffect(() => {
    if (!enabled || !threadId) {
      setItems(EMPTY);
      return;
    }
    let cancelled = false;
    setItems(EMPTY);
    fetch(API_ENDPOINTS.threadHighlights(threadId), { credentials: "include", cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data || !Array.isArray(data.highlights)) return;
        setItems(data.highlights as StoredHighlight[]);
      })
      .catch(() => {
        /* highlights simply do not show; the message renders as before */
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, enabled]);

  const create = useCallback<HighlightActions["create"]>(
    async (input) => {
      if (!threadId) return null;
      try {
        const res = await fetch(API_ENDPOINTS.threadHighlights(threadId), {
          credentials: "include",
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note: "", ...input }),
        });
        if (!res.ok) {
          console.warn(`[highlights] create failed: ${res.status}`);
          return null;
        }
        const data = await res.json();
        const h = data.highlight as StoredHighlight;
        setItems((prev) => [...prev, h]);
        return h;
      } catch (err) {
        console.warn("[highlights] create failed", err);
        return null;
      }
    },
    [threadId],
  );

  const update = useCallback<HighlightActions["update"]>(
    async (id, patch) => {
      if (!threadId) return null;
      try {
        const res = await fetch(API_ENDPOINTS.threadHighlight(threadId, id), {
          credentials: "include",
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
        if (!res.ok) {
          console.warn(`[highlights] update failed: ${res.status}`);
          return null;
        }
        const data = await res.json();
        const h = data.highlight as StoredHighlight;
        setItems((prev) => prev.map((x) => (x.id === id ? h : x)));
        return h;
      } catch (err) {
        console.warn("[highlights] update failed", err);
        return null;
      }
    },
    [threadId],
  );

  const remove = useCallback<HighlightActions["remove"]>(
    async (id) => {
      if (!threadId) return false;
      try {
        const res = await fetch(API_ENDPOINTS.threadHighlight(threadId, id), {
          credentials: "include",
          method: "DELETE",
        });
        if (!res.ok) {
          console.warn(`[highlights] delete failed: ${res.status}`);
          return false;
        }
        setItems((prev) => prev.filter((x) => x.id !== id));
        return true;
      } catch (err) {
        console.warn("[highlights] delete failed", err);
        return false;
      }
    },
    [threadId],
  );

  const byMessage = useMemo(() => {
    const map = new Map<string, StoredHighlight[]>();
    for (const h of items) {
      const list = map.get(h.messageId);
      if (list) list.push(h);
      else map.set(h.messageId, [h]);
    }
    return map;
  }, [items]);

  const actions = useMemo(() => ({ create, update, remove }), [create, update, remove]);

  return useMemo(
    () => (enabled && threadId ? { items, byMessage, actions } : null),
    [enabled, threadId, items, byMessage, actions],
  );
}
