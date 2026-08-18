"use client";

/**
 * Per-message highlight layer for a COMPLETED assistant message.
 *
 * Returns null unless the feature is on, the message has a server id (only
 * persisted, finished messages do -- a streaming message never does), and the
 * text handed to the renderer is the stored content verbatim. With null the
 * caller renders exactly what it rendered before this feature existed.
 *
 * Otherwise it hands back: the rehype plugin that draws stored highlights and
 * reports the text layout, a ref + mouseup/click handlers for the message
 * body, and the popover node. Selection -> popover happens on mouseup only
 * (mid-drag nothing appears); a click on an existing <mark> opens its editor.
 */
import React, { useCallback, useMemo, useRef, useState } from "react";

import type { SourceRange } from "./anchoring";
import { resolveDomSelection } from "./dom-selection";
import HighlightPopover, { type HighlightColour, type PopoverAnchor } from "./highlight-popover";
import { rehypeHighlights, type LayoutReport, type RehypeHighlightsOptions } from "./rehype-highlights";
import type { HighlightActions, StoredHighlight } from "./use-thread-highlights";
import { toast } from "../toaster";

type PopoverState =
  | { mode: "create"; range: SourceRange; anchor: PopoverAnchor; colour: HighlightColour }
  | { mode: "edit"; highlightId: string; anchor: PopoverAnchor };

export type MessageHighlightLayer = {
  rehypePlugin: [typeof rehypeHighlights, RehypeHighlightsOptions];
  bodyRef: React.RefObject<HTMLDivElement | null>;
  onMouseUp: (e: React.MouseEvent<HTMLDivElement>) => void;
  onClick: (e: React.MouseEvent<HTMLDivElement>) => void;
  popover: React.ReactNode;
};

type Args = {
  enabled: boolean;
  messageId?: string;
  /** Exactly the string given to <Markdown>. */
  source: string;
  highlights: StoredHighlight[];
  actions: HighlightActions | null;
};

const rectToAnchor = (rect: DOMRect | null, fallback: { x: number; y: number }): PopoverAnchor =>
  rect && (rect.width > 0 || rect.height > 0)
    ? { top: rect.top, left: rect.left, bottom: rect.bottom, width: rect.width }
    : { top: fallback.y, left: fallback.x, bottom: fallback.y, width: 0 };

export function useMessageHighlights({ enabled, messageId, source, highlights, actions }: Args): MessageHighlightLayer | null {
  const active = enabled && !!messageId && !!actions;
  const layoutRef = useRef<LayoutReport | null>(null);
  const warnedRef = useRef<Set<string>>(new Set());
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [popover, setPopover] = useState<PopoverState | null>(null);
  const [busy, setBusy] = useState(false);

  const onLayout = useCallback(
    (report: LayoutReport) => {
      layoutRef.current = report;
      for (const id of report.invalid) {
        if (warnedRef.current.has(id)) continue;
        warnedRef.current.add(id);
        console.warn(
          `[highlights] not rendering highlight ${id} on message ${messageId}: ` +
            "its stored text no longer matches the rendered text at its offsets",
        );
      }
    },
    [messageId],
  );

  const rehypePlugin = useMemo<[typeof rehypeHighlights, RehypeHighlightsOptions]>(
    () => [
      rehypeHighlights,
      {
        source,
        highlights: highlights.map((h) => ({
          id: h.id,
          startOffset: h.startOffset,
          endOffset: h.endOffset,
          selectedText: h.selectedText,
          colour: h.colour,
          note: h.note,
          createdAt: h.createdAt,
        })),
        onLayout,
      },
    ],
    [source, highlights, onLayout],
  );

  const onMouseUp = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!active) return;
      const fallback = { x: e.clientX, y: e.clientY };
      // Let the browser finalise the selection before reading it.
      window.setTimeout(() => {
        const body = bodyRef.current;
        const layout = layoutRef.current;
        if (!body || !layout) return;
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) return; // plain click -> onClick
        const reasons: string[] = [];
        const resolved = resolveDomSelection(body, sel, layout.fragments, (r) => reasons.push(r));
        if (!resolved) {
          if (reasons[0] && reasons[0] !== "outside" && reasons[0] !== "collapsed") {
            console.debug(`[highlights] selection not highlightable (${reasons.join(", ")})`);
          }
          return;
        }
        setPopover({
          mode: "create",
          range: resolved.range,
          anchor: rectToAnchor(resolved.rect, fallback),
          colour: "yellow",
        });
      }, 0);
    },
    [active],
  );

  const onClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!active) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return; // a drag that ended on a mark: creation path
      const target = e.target as HTMLElement | null;
      const mark = target?.closest?.("mark[data-hl-id]") as HTMLElement | null;
      if (!mark || !bodyRef.current?.contains(mark)) return;
      const id = mark.getAttribute("data-hl-id");
      if (!id) return;
      setPopover({ mode: "edit", highlightId: id, anchor: rectToAnchor(mark.getBoundingClientRect(), { x: e.clientX, y: e.clientY }) });
    },
    [active],
  );

  const close = useCallback(() => setPopover(null), []);

  const pickColour = useCallback(
    async (colour: HighlightColour) => {
      if (!popover || !actions || !messageId) return;
      setBusy(true);
      try {
        if (popover.mode === "create") {
          const created = await actions.create({
            messageId,
            startOffset: popover.range.start,
            endOffset: popover.range.end,
            selectedText: popover.range.selectedText,
            colour,
          });
          if (!created) {
            toast("The highlight could not be saved. Please try again.");
            setPopover(null);
            return;
          }
          window.getSelection()?.removeAllRanges();
          setPopover({ mode: "edit", highlightId: created.id, anchor: popover.anchor });
        } else {
          const updated = await actions.update(popover.highlightId, { colour });
          if (!updated) toast("The highlight could not be updated. Please try again.");
        }
      } finally {
        setBusy(false);
      }
    },
    [popover, actions, messageId],
  );

  const saveNote = useCallback(
    async (note: string) => {
      if (!popover || popover.mode !== "edit" || !actions) return;
      setBusy(true);
      try {
        const updated = await actions.update(popover.highlightId, { note });
        if (!updated) toast("The note could not be saved. Please try again.");
      } finally {
        setBusy(false);
      }
    },
    [popover, actions],
  );

  const remove = useCallback(async () => {
    if (!popover || popover.mode !== "edit" || !actions) return;
    setBusy(true);
    try {
      const ok = await actions.remove(popover.highlightId);
      if (!ok) toast("The highlight could not be removed. Please try again.");
      else setPopover(null);
    } finally {
      setBusy(false);
    }
  }, [popover, actions]);

  if (!active) return null;

  let popoverNode: React.ReactNode = null;
  if (popover?.mode === "create") {
    popoverNode = (
      <HighlightPopover
        anchor={popover.anchor}
        mode="create"
        colour={popover.colour}
        note=""
        busy={busy}
        onPickColour={pickColour}
        onSaveNote={() => {}}
        onDelete={() => {}}
        onClose={close}
      />
    );
  } else if (popover?.mode === "edit") {
    const h = highlights.find((x) => x.id === popover.highlightId);
    if (h) {
      popoverNode = (
        <HighlightPopover
          anchor={popover.anchor}
          mode="edit"
          colour={h.colour}
          note={h.note}
          busy={busy}
          onPickColour={pickColour}
          onSaveNote={saveNote}
          onDelete={remove}
          onClose={close}
        />
      );
    }
  }

  return { rehypePlugin, bodyRef, onMouseUp, onClick, popover: popoverNode };
}
