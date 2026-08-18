"use client";

/**
 * Thread-wide highlights panel (HIGHLIGHTS_ENABLED) -- a persistent side
 * column beside the conversation, following the Thread documents panel
 * (thread-documents.tsx): a flex column at >= 1100px, an overlay with backdrop
 * + Escape below that. Fixed 360px, no resize, no persisted width. Mutually
 * exclusive with the documents panel (chat.tsx owns that).
 *
 * Lists every highlight in the thread ordered by message (thread order), then
 * by position within the message. Each row: colour swatch, highlighted text,
 * note, date. Click a row -> scroll to its <mark> in the conversation and
 * flash it. Delete (two-step, uses the existing DELETE route through
 * actions.remove). Two export buttons download the Phase 1 endpoint's
 * Markdown / Excel via the same blob pattern GeoPilot uses.
 *
 * All state and handlers stay owned by chat.tsx and arrive as props.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Download, FileSpreadsheet, Trash2, X } from "lucide-react";

import s from "./highlights-panel.module.css";
import type { StoredHighlight } from "./use-thread-highlights";
import { API_ENDPOINTS } from "../../config/api";
import { toast } from "../toaster";
import { THREAD_DOCS_OVERLAY_BREAKPOINT } from "../thread-documents";

export const HIGHLIGHTS_PANEL_WIDTH = 360;
const FLASH_MS = 1600;

type Props = {
  open: boolean;
  onClose: () => void;
  threadId: string;
  threadTitle: string;
  items: StoredHighlight[];
  /** Thread order of message ids (assistant + user); unknown ids sort last. */
  messageIds: string[];
  onDelete: (id: string) => Promise<boolean>;
};

const formatDate = (iso: string): string => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};

/** Panel order: message index (thread order), then offset, then createdAt. */
export function orderHighlights(items: StoredHighlight[], messageIds: string[]): StoredHighlight[] {
  const index = new Map(messageIds.map((id, i) => [id, i]));
  const rank = (h: StoredHighlight) => index.get(h.messageId) ?? Number.MAX_SAFE_INTEGER;
  return [...items].sort((a, b) =>
    rank(a) - rank(b) || a.startOffset - b.startOffset || a.createdAt.localeCompare(b.createdAt),
  );
}

/** Scroll to the highlight's mark(s) in the conversation and flash them. */
export function jumpToHighlight(id: string): boolean {
  const marks = Array.from(document.querySelectorAll<HTMLElement>(`mark[data-hl-id="${CSS.escape(id)}"]`));
  if (marks.length === 0) return false;
  marks[0].scrollIntoView({ behavior: "smooth", block: "center" });
  for (const m of marks) {
    m.classList.remove("hlFlash");
    // Restart the animation even if it is mid-flight.
    void m.offsetWidth;
    m.classList.add("hlFlash");
    window.setTimeout(() => m.classList.remove("hlFlash"), FLASH_MS);
  }
  return true;
}

/** Download an export via fetch + blob (same pattern as GeoPilot's xlsx). */
export async function downloadExport(threadId: string, format: "md" | "xlsx"): Promise<void> {
  const res = await fetch(API_ENDPOINTS.threadHighlightsExport(threadId, format), { credentials: "include" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as any)?.detail || `Export failed (HTTP ${res.status}).`);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const match = cd.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] || `highlights.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function HighlightsPanel({ open, onClose, threadId, threadTitle, items, messageIds, onDelete }: Props) {
  const [overlay, setOverlay] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"md" | "xlsx" | null>(null);

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${THREAD_DOCS_OVERLAY_BREAKPOINT - 1}px)`);
    const apply = () => setOverlay(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    if (!open || !overlay) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, overlay, onClose]);

  const ordered = useMemo(() => orderHighlights(items, messageIds), [items, messageIds]);

  const handleJump = useCallback(
    (id: string) => {
      if (!jumpToHighlight(id)) {
        toast("This highlight isn't shown in the conversation — its text no longer matches the message.");
        return;
      }
      if (overlay) onClose();
    },
    [overlay, onClose],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      if (confirmId !== id) {
        setConfirmId(id);
        return;
      }
      setBusyId(id);
      try {
        const ok = await onDelete(id);
        if (!ok) toast("The highlight could not be removed. Please try again.");
      } finally {
        setBusyId(null);
        setConfirmId(null);
      }
    },
    [confirmId, onDelete],
  );

  const handleExport = useCallback(
    async (format: "md" | "xlsx") => {
      setExporting(format);
      try {
        await downloadExport(threadId, format);
      } catch (e: any) {
        toast(e?.message ?? "Export failed.");
      } finally {
        setExporting(null);
      }
    },
    [threadId],
  );

  if (!open) return null;

  return (
    <>
      {overlay && <div className={s.backdrop} onMouseDown={onClose} />}
      <aside
        className={`${s.panel} ${overlay ? s.panelOverlay : ""}`}
        style={{ width: HIGHLIGHTS_PANEL_WIDTH }}
        aria-label="Highlights"
        data-testid="highlights-panel"
      >
        <header className={s.header}>
          <div className={s.headerText}>
            <span className={s.title}>Highlights</span>
            <span className={s.count}>
              {ordered.length} in {threadTitle || "this conversation"}
            </span>
          </div>
          <button type="button" className={s.iconBtn} aria-label="Close highlights" onClick={onClose}>
            <X size={14} strokeWidth={1.5} />
          </button>
        </header>

        <div className={s.exportRow}>
          <button
            type="button"
            className={s.exportBtn}
            disabled={exporting !== null}
            onClick={() => handleExport("md")}
            title="Download as Markdown"
          >
            <Download size={13} strokeWidth={1.5} />
            {exporting === "md" ? "Exporting…" : "Markdown"}
          </button>
          <button
            type="button"
            className={s.exportBtn}
            disabled={exporting !== null}
            onClick={() => handleExport("xlsx")}
            title="Download as Excel"
          >
            <FileSpreadsheet size={13} strokeWidth={1.5} />
            {exporting === "xlsx" ? "Exporting…" : "Excel"}
          </button>
        </div>

        {ordered.length === 0 ? (
          <p className={s.empty}>
            No highlights yet. Select text in an answer and pick a colour to add one.
          </p>
        ) : (
          <ul className={s.list}>
            {ordered.map((h) => (
              <li key={h.id} className={s.row}>
                <button
                  type="button"
                  className={s.rowMain}
                  onClick={() => handleJump(h.id)}
                  title="Show in conversation"
                >
                  <span className={`${s.swatch} ${s[`swatch_${h.colour}`] ?? ""}`} aria-hidden="true" />
                  <span className={s.rowBody}>
                    <span className={s.text}>{h.selectedText}</span>
                    {h.note.trim() !== "" && <span className={s.note}>{h.note}</span>}
                    <span className={s.meta}>
                      {h.colour} · {formatDate(h.createdAt)}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  className={`${s.deleteBtn} ${confirmId === h.id ? s.deleteConfirm : ""}`}
                  aria-label={confirmId === h.id ? "Confirm delete highlight" : "Delete highlight"}
                  title={confirmId === h.id ? "Click again to delete" : "Delete highlight"}
                  disabled={busyId === h.id}
                  onClick={() => handleDelete(h.id)}
                  onBlur={() => confirmId === h.id && busyId !== h.id && setConfirmId(null)}
                >
                  {confirmId === h.id ? "Delete?" : <Trash2 size={13} strokeWidth={1.5} />}
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </>
  );
}
