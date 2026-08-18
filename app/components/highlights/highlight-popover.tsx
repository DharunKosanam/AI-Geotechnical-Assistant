"use client";

/**
 * Small floating panel anchored to a selection or an existing highlight.
 * Follows the account-menu conventions: closes on Escape and outside
 * mousedown, takes focus on open. position: fixed, clamped to the viewport,
 * above the anchor when there is room, else below.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";

import s from "./highlight-popover.module.css";

export const HIGHLIGHT_COLOURS = ["yellow", "green", "blue", "pink"] as const;
export type HighlightColour = (typeof HIGHLIGHT_COLOURS)[number];

export type PopoverAnchor = { top: number; left: number; bottom: number; width: number };

type Props = {
  anchor: PopoverAnchor;
  mode: "create" | "edit";
  colour: string;
  note: string;
  busy?: boolean;
  onPickColour: (colour: HighlightColour) => void;
  onSaveNote: (note: string) => void;
  onDelete: () => void;
  onClose: () => void;
};

const GAP = 8;
const MARGIN = 8;

export default function HighlightPopover({
  anchor,
  mode,
  colour,
  note,
  busy,
  onPickColour,
  onSaveNote,
  onDelete,
  onClose,
}: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const [draft, setDraft] = useState(note);

  useEffect(() => setDraft(note), [note]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let top = anchor.top - h - GAP;
    if (top < MARGIN) top = Math.min(anchor.bottom + GAP, vh - h - MARGIN);
    let left = anchor.left + anchor.width / 2 - w / 2;
    left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));
    setPos({ top, left });
  }, [anchor, mode]);

  useEffect(() => {
    // preventScroll: a scroll here would trip the close-on-scroll listener.
    ref.current?.focus({ preventScroll: true });
  }, [mode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    // Any scroll moves the anchor out from under a fixed panel: close --
    // except scrolling inside the panel itself (a long note in the textarea).
    const onScroll = (e: Event) => {
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className={s.popover}
      role="dialog"
      aria-label={mode === "create" ? "Highlight selection" : "Edit highlight"}
      tabIndex={-1}
      style={pos ? { top: pos.top, left: pos.left } : { top: -9999, left: -9999 }}
      // Keep the text selection: a mousedown inside would collapse it.
      onMouseDown={(e) => {
        const t = e.target as HTMLElement;
        if (t.tagName !== "TEXTAREA" && t.tagName !== "BUTTON") e.preventDefault();
      }}
    >
      <div className={s.row}>
        <div className={s.swatches} role="group" aria-label="Highlight colour">
          {HIGHLIGHT_COLOURS.map((c) => (
            <button
              key={c}
              type="button"
              className={`${s.swatch} ${s[`swatch_${c}`]} ${c === colour ? s.swatchActive : ""}`}
              aria-label={`${c} highlight`}
              aria-pressed={c === colour}
              disabled={busy}
              onClick={() => onPickColour(c)}
            />
          ))}
        </div>
        {mode === "edit" && (
          <button
            type="button"
            className={s.iconBtn}
            title="Remove highlight"
            aria-label="Remove highlight"
            disabled={busy}
            onClick={onDelete}
          >
            <Trash2 size={13} strokeWidth={1.5} />
          </button>
        )}
      </div>
      {mode === "create" ? (
        <p className={s.hint}>Pick a colour to highlight</p>
      ) : (
        <>
          <textarea
            className={s.note}
            placeholder="Add a note…"
            value={draft}
            rows={3}
            maxLength={2000}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") onSaveNote(draft);
            }}
          />
          <div className={s.actions}>
            <button type="button" className={s.btn} disabled={busy} onClick={onClose}>
              Close
            </button>
            <button
              type="button"
              className={`${s.btn} ${s.btnPrimary}`}
              disabled={busy || draft === note}
              onClick={() => onSaveNote(draft)}
            >
              Save note
            </button>
          </div>
        </>
      )}
    </div>
  );
}
