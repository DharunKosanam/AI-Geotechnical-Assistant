"use client";

/**
 * Thread documents panel — the thread's source set (SOURCE_SETS_ENABLED),
 * as a persistent side column (Claude-artifacts pattern), NOT a modal.
 *
 *   - Wide viewports (>= 1100px): a real flex column in the chat row. Opening
 *     it narrows the conversation; both stay fully usable. No backdrop, no
 *     focus trap, no Escape. Resizable via the left-edge handle; open state
 *     and width persist in localStorage as a layout preference.
 *   - Narrow viewports (< 1100px): three columns no longer fit, so the panel
 *     overlays the conversation with a backdrop and closes on Escape /
 *     backdrop click. (See DEPLOY-CHECKLIST for the reasoning.)
 *
 * The ONLY modal region is the destructive confirm: once the mandatory
 * dry-run preview is showing, focus lands on Cancel, Tab cycles Cancel <->
 * Delete, and Escape cancels. requestRemoveSource fires the preview; the
 * destructive confirmRemoveSource is reachable only from that block.
 *
 * Deliberately NOT the same thing as an answer's "Grounded in" panel: this
 * lists every document uploaded INTO the thread with ingest status/section
 * counts and hosts Remove; "Grounded in" (message-list.tsx) lists the sources
 * behind ONE answer. Different container, position, header language, and no
 * citation indices here.
 *
 * All state and handlers stay owned by chat.tsx and arrive as props.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import s from "./thread-documents.module.css";

type ThreadSource = {
  filename: string;
  status: string;
  reason: string | null;
  chunkCount: number;
  provenance: "verbatim" | "vision" | "mixed";
  visionPages: number[];
  partiallyIndexed: boolean;
  warning: string | null;
};

type RemovePreview = {
  filename: string;
  chunksToDelete: number;
  parentDocsToDelete: number;
};

type ThreadDocumentsProps = {
  open: boolean;
  onClose: () => void;
  /* null = not loaded yet; [] = loaded and empty */
  sources: ThreadSource[] | null;
  removePreview: RemovePreview | null;
  removeBusy: boolean;
  /* Remove is unavailable while a generation/stream is running. */
  removeLocked: boolean;
  requestRemoveSource: (filename: string) => void;
  confirmRemoveSource: () => void;
  cancelRemove: () => void;
};

/* Layout-preference persistence (not per-thread state). */
export const THREAD_DOCS_OPEN_KEY = "geotech.threadDocs.open";
const THREAD_DOCS_WIDTH_KEY = "geotech.threadDocs.width";
const WIDTH_DEFAULT = 360;
const WIDTH_MIN = 280;
const WIDTH_MAX = 560;
/* The chat column must keep at least this much room when resizing. */
const CHAT_MIN = 480;
/* Below this the panel overlays instead of taking a column: 264 sidebar +
   280 panel min + ~500 for a usable chat column ≈ 1044, rounded up for slack
   and kept well clear of the 720px sidebar-overlay breakpoint. */
export const THREAD_DOCS_OVERLAY_BREAKPOINT = 1100;

const clampWidth = (w: number) => {
  const viewportCap =
    typeof window === "undefined" ? WIDTH_MAX : Math.max(WIDTH_MIN, window.innerWidth - CHAT_MIN);
  return Math.min(WIDTH_MAX, viewportCap, Math.max(WIDTH_MIN, Math.round(w)));
};

const statusLabel = (d: ThreadSource): string => {
  if (d.status === "ready") return `${d.chunkCount} ${d.chunkCount === 1 ? "section" : "sections"}`;
  if (d.status === "pending") return "processing";
  return `failed${d.reason ? ` · ${d.reason}` : ""}`;
};

const provenanceLabel = (d: ThreadSource): string => {
  if (d.provenance === "vision") return "AI vision-derived";
  if (d.provenance === "mixed") return `text + AI vision (p. ${d.visionPages.join(", ")})`;
  return "verbatim text";
};

/* The destructive confirm — the one modal region. */
const RemoveConfirm = ({
  preview,
  busy,
  onCancel,
  onConfirm,
}: {
  preview: RemovePreview;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) => {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const deleteRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    // Focus lands on the safe choice; Tab cycles inside; Escape cancels.
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      const a = cancelRef.current;
      const b = deleteRef.current;
      if (!a || !b) return;
      if (e.shiftKey && document.activeElement === a) {
        e.preventDefault();
        b.focus();
      } else if (!e.shiftKey && document.activeElement === b) {
        e.preventDefault();
        a.focus();
      } else if (document.activeElement !== a && document.activeElement !== b) {
        e.preventDefault();
        a.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className={s.confirm} role="alertdialog" aria-modal="true" aria-live="assertive">
      <p className={s.confirmText}>
        Delete <strong>{preview.chunksToDelete}</strong>{" "}
        {preview.chunksToDelete === 1 ? "section" : "sections"} and{" "}
        <strong>{preview.parentDocsToDelete}</strong> document{" "}
        {preview.parentDocsToDelete === 1 ? "record" : "records"} from this thread? Other
        conversations and the knowledge base are not affected.
      </p>
      <div className={s.confirmActions}>
        <button ref={cancelRef} type="button" className={s.cancelBtn} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button ref={deleteRef} type="button" className={s.deleteBtn} disabled={busy} onClick={onConfirm}>
          {busy ? "Deleting…" : "Delete"}
        </button>
      </div>
    </div>
  );
};

export default function ThreadDocuments({
  open,
  onClose,
  sources,
  removePreview,
  removeBusy,
  removeLocked,
  requestRemoveSource,
  confirmRemoveSource,
  cancelRemove,
}: ThreadDocumentsProps) {
  const [width, setWidth] = useState(WIDTH_DEFAULT);
  const [overlay, setOverlay] = useState(false);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  // Restore persisted width (client-only, after mount → no hydration mismatch).
  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem(THREAD_DOCS_WIDTH_KEY));
      if (saved) setWidth(clampWidth(saved));
    } catch {
      /* storage unavailable — keep default */
    }
  }, []);

  // Column vs overlay mode follows the viewport.
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${THREAD_DOCS_OVERLAY_BREAKPOINT - 1}px)`);
    const apply = () => setOverlay(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  // Re-clamp the column width if the window shrinks under it.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Overlay mode only: Escape closes the panel (column mode: never).
  useEffect(() => {
    if (!open || !overlay || removePreview) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, overlay, removePreview, onClose]);

  const persistWidth = useCallback((w: number) => {
    try {
      window.localStorage.setItem(THREAD_DOCS_WIDTH_KEY, String(w));
    } catch {
      /* ignore */
    }
  }, []);

  // Drag-to-resize on the left edge (column mode only).
  const onHandlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (overlay) return;
    dragRef.current = { startX: e.clientX, startW: width };
    (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };
  const onHandlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    // Handle is on the LEFT edge: dragging left grows the panel.
    setWidth(clampWidth(d.startW + (d.startX - e.clientX)));
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    try {
      (e.currentTarget as HTMLDivElement).releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    persistWidth(clampWidth(width));
  };
  const onHandleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (overlay) return;
    const step = 16;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const next = clampWidth(width + (e.key === "ArrowLeft" ? step : -step));
      setWidth(next);
      persistWidth(next);
    }
  };

  if (!open) return null;

  return (
    <>
      {overlay && <div className={s.backdrop} onMouseDown={onClose} />}
      <aside
        className={`${s.panel} ${overlay ? s.panelOverlay : ""}`}
        style={overlay ? undefined : { width }}
        role="complementary"
        aria-label="Thread documents"
      >
        {!overlay && (
          <div
            className={s.resizeHandle}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize thread documents panel"
            aria-valuemin={WIDTH_MIN}
            aria-valuemax={WIDTH_MAX}
            aria-valuenow={width}
            tabIndex={0}
            onPointerDown={onHandlePointerDown}
            onPointerMove={onHandlePointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onKeyDown={onHandleKeyDown}
          />
        )}
        <header className={s.header}>
          <div className={s.headerText}>
            <h2 className={s.title}>Thread documents</h2>
            <p className={s.subtitle}>
              Files uploaded into this thread. Removing one deletes its indexed
              sections from this thread only.
            </p>
          </div>
          <button
            type="button"
            className={s.closeBtn}
            onClick={onClose}
            aria-label="Close thread documents"
            title="Close"
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </header>

        <div className={s.body}>
          {sources === null && (
            <p className={s.note} role="status">Loading documents…</p>
          )}
          {sources !== null && sources.length === 0 && (
            <p className={s.note}>
              No documents in this thread yet. Attach a file from the composer to add one.
            </p>
          )}
          {sources !== null && sources.length > 0 && (
            <ul className={s.list}>
              {sources.map((d) => {
                const previewing = removePreview?.filename === d.filename;
                const bad = d.status === "failed" || d.status === "error";
                return (
                  <li key={d.filename} className={s.row}>
                    <div className={s.rowMain}>
                      <span className={s.filename} title={d.filename}>{d.filename}</span>
                      <span className={s.meta}>
                        <span className={bad ? s.metaBad : undefined}>{statusLabel(d)}</span>
                        {" · "}
                        {provenanceLabel(d)}
                        {d.partiallyIndexed && d.warning ? ` · ${d.warning}` : ""}
                      </span>
                    </div>
                    {previewing ? (
                      <RemoveConfirm
                        preview={removePreview!}
                        busy={removeBusy}
                        onCancel={cancelRemove}
                        onConfirm={confirmRemoveSource}
                      />
                    ) : (
                      <span
                        className={s.removeWrap}
                        title={
                          removeLocked
                            ? "Available once the current response or generation finishes"
                            : `Preview what removing ${d.filename} would delete`
                        }
                      >
                        <button
                          type="button"
                          className={s.removeBtn}
                          disabled={removeBusy || removeLocked || !!removePreview}
                          onClick={() => requestRemoveSource(d.filename)}
                        >
                          Remove…
                        </button>
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
