"use client";

/**
 * Composer + attachment surfaces (dark redesign). Every handler and piece of
 * state stays owned by chat.tsx and arrives as props under its original name —
 * this component only adds presentation (auto-grow, tokens, layout).
 *
 * The scope toggle the mockup shows has no backend (retrieval scope is the
 * router's decision, not a user control), so it renders disabled with a
 * tooltip.
 */
import React, { useEffect } from "react";
import DiagramEditorModal from "./diagram-editor-modal";
import { API_ENDPOINTS } from "../config/api";
import {
  AlertCircle,
  ArrowUp,
  Check,
  Database,
  File as FileIcon,
  Loader2,
  PenLine,
  Plus,
  X,
} from "lucide-react";
import c from "./composer.module.css";
import { toast } from "./toaster";

const STAGE_LABELS: Record<string, string> = {
  extracting: "Extracting...",
  ocr: "OCR...",
  chunking: "Chunking...",
  embedding: "Embedding...",
  done: "Done",
};
const stageLabel = (stage?: string): string =>
  (stage && STAGE_LABELS[stage]) || "Processing...";

type ComposerProps = {
  onSubmit: (e: React.FormEvent) => void;
  showJoinModal: boolean;
  setShowJoinModal: (v: boolean) => void;
  joinThreadInput: string;
  setJoinThreadInput: (v: string) => void;
  handleJoinTeam: () => void;
  showDiagramEditor: boolean;
  setShowDiagramEditor: (v: boolean) => void;
  uploadDiagram: (pngDataUri: string, xml: string) => void;
  fileInputRef: React.MutableRefObject<HTMLInputElement | null>;
  uploadTypes: { extensions: string[]; label: string };
  handleFileAttach: (e: React.ChangeEvent<HTMLInputElement>) => void;
  attachedFiles: any[];
  removeAttachedFile: (id: string) => void;
  isUploading: boolean;
  sourceSetsEnabled: boolean;
  threadId: string | null;
  showSources: boolean;
  setShowSources: React.Dispatch<React.SetStateAction<boolean>>;
  threadSources: any[] | null;
  removePreview: any;
  setRemovePreview: (v: any) => void;
  removeBusy: boolean;
  formatBusy: boolean;
  isStreaming: boolean;
  requestRemoveSource: (filename: string) => void;
  confirmRemoveSource: () => void;
  availableFormats: { key: string; label: string }[] | null;
  hasReadyFormatDocs: boolean;
  generateFormatDocument: (key: string, label: string) => void;
  formatProgress: string | null;
  diagramEditorEnabled: boolean;
  showAttachMenu: boolean;
  setShowAttachMenu: React.Dispatch<React.SetStateAction<boolean>>;
  textareaRef: React.MutableRefObject<HTMLTextAreaElement | null>;
  userInput: string;
  setUserInput: (v: string) => void;
  handleKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  inputDisabled: boolean;
};

const Composer = ({
  onSubmit: handleSubmit,
  showJoinModal,
  setShowJoinModal,
  joinThreadInput,
  setJoinThreadInput,
  handleJoinTeam,
  showDiagramEditor,
  setShowDiagramEditor,
  uploadDiagram,
  fileInputRef,
  uploadTypes,
  handleFileAttach,
  attachedFiles,
  removeAttachedFile,
  isUploading,
  sourceSetsEnabled,
  threadId,
  showSources,
  setShowSources,
  threadSources,
  removePreview,
  setRemovePreview,
  removeBusy,
  formatBusy,
  isStreaming,
  requestRemoveSource,
  confirmRemoveSource,
  availableFormats,
  hasReadyFormatDocs,
  generateFormatDocument,
  formatProgress,
  diagramEditorEnabled,
  showAttachMenu,
  setShowAttachMenu,
  textareaRef,
  userInput,
  setUserInput,
  handleKeyDown,
  inputDisabled,
}: ComposerProps) => {
  // Auto-grow: track content height up to a cap; also resets when the send
  // path clears userInput.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [userInput, textareaRef]);

  // Escape dismisses the attach menu.
  useEffect(() => {
    if (!showAttachMenu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowAttachMenu(() => false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [showAttachMenu, setShowAttachMenu]);

  // Join modal: trap Tab inside the dialog while open; restore focus to the
  // opener on close. (Escape-close is handled by chat.tsx, which owns the
  // state.)
  const joinModalRef = React.useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!showJoinModal) return;
    const opener = document.activeElement as HTMLElement | null;
    const FOCUSABLE =
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusables = joinModalRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [showJoinModal]);

  const sendDisabled = inputDisabled || !userInput.trim();

  return (
    <form onSubmit={handleSubmit} className={c.composerArea}>
      {showJoinModal && (
        <div className={c.modal}>
          <div className={c.modalContent} role="dialog" aria-modal="true" ref={joinModalRef}>
            <h3>Join team chat</h3>
            <input
              type="text"
              value={joinThreadInput}
              onChange={(e) => setJoinThreadInput(e.target.value)}
              placeholder="Enter team thread ID"
              autoFocus
            />
            <div className={c.modalButtons}>
              <button type="button" onClick={() => setShowJoinModal(false)}>
                Cancel
              </button>
              <button type="button" onClick={handleJoinTeam}>
                Join
              </button>
            </div>
          </div>
        </div>
      )}
      {showDiagramEditor && (
        <DiagramEditorModal
          onExport={(pngDataUri, xml) => {
            setShowDiagramEditor(false);
            uploadDiagram(pngDataUri, xml);
          }}
          onClose={() => setShowDiagramEditor(false)}
          onError={(message) => {
            setShowDiagramEditor(false);
            toast(message);
          }}
        />
      )}
      {/* Hidden file input — kept in the DOM, triggered by the "+" button. */}
      <input
        type="file"
        ref={fileInputRef}
        className="hidden"
        accept={uploadTypes.extensions.join(",")}
        multiple
        onChange={handleFileAttach}
      />

      <div className={c.composerInner}>
        {sourceSetsEnabled && threadId && (
          <div className={c.sourcesBar}>
            <button
              type="button"
              className={c.sourcesToggle}
              onClick={() => setShowSources((s) => !s)}
              aria-expanded={showSources}
            >
              {showSources ? "Hide sources" : `Sources (${threadSources?.length ?? 0})`}
            </button>
            {showSources && (
              <div className={c.sourcesPanel}>
                {(threadSources ?? []).length === 0 && (
                  <div className={c.sourcesEmpty}>
                    No sources in this conversation yet. Attach a file to add one.
                  </div>
                )}
                {(threadSources ?? []).map((s) => (
                  <div key={s.filename} className={c.sourceRow}>
                    <span className={c.sourceName} title={s.filename}>
                      {s.filename}
                    </span>
                    <span className={c.sourceMeta}>
                      {s.status === "ready"
                        ? `${s.chunkCount} sections`
                        : s.status === "pending"
                          ? "processing..."
                          : `failed${s.reason ? `: ${s.reason}` : ""}`}
                      {" · "}
                      {s.provenance === "vision"
                        ? "AI vision-derived"
                        : s.provenance === "mixed"
                          ? `text + AI vision (pages ${s.visionPages.join(", ")})`
                          : "verbatim text"}
                      {s.partiallyIndexed && s.warning ? ` · ${s.warning}` : ""}
                    </span>
                    {removePreview?.filename === s.filename ? (
                      <span className={c.sourceConfirm}>
                        Delete {removePreview.chunksToDelete} sections and{" "}
                        {removePreview.parentDocsToDelete} document record? Other
                        conversations and the knowledge base are not affected.
                        <button
                          type="button"
                          className={c.sourceConfirmBtn}
                          disabled={removeBusy}
                          onClick={confirmRemoveSource}
                        >
                          Delete
                        </button>
                        <button
                          type="button"
                          className={c.sourceCancelBtn}
                          disabled={removeBusy}
                          onClick={() => setRemovePreview(null)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className={c.sourceRemoveBtn}
                        disabled={removeBusy || formatBusy || isStreaming}
                        title={`Remove ${s.filename} from this source set`}
                        onClick={() => requestRemoveSource(s.filename)}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {availableFormats && hasReadyFormatDocs && (
          <div className={c.formatButtons}>
            <span className={c.formatButtonsLabel}>Generate from sources:</span>
            {availableFormats.map((f) => {
              const disabled = formatBusy || isStreaming || inputDisabled || isUploading;
              // Why disabled matters more than what it does: say which wait
              // the user is in. The wrapper span carries the tooltip too,
              // because a disabled button does not reliably hover in every
              // browser.
              const tooltip = formatBusy
                ? "A document generation is already running — try again shortly."
                : isUploading
                  ? "A document is still processing — available once it's ready."
                  : disabled
                    ? "Waiting for the current response to finish."
                    : `Generate a ${f.label.toLowerCase()} from this conversation's documents — takes a few minutes for a large document`;
              return (
                <span key={f.key} className={c.formatBtnWrap} title={tooltip}>
                  <button
                    type="button"
                    className={c.formatBtn}
                    disabled={disabled}
                    title={tooltip}
                    onClick={() => generateFormatDocument(f.key, f.label)}
                  >
                    {f.label}
                  </button>
                </span>
              );
            })}
          </div>
        )}

        {formatProgress && (
          <div className={c.formatProgressNotice} role="status" aria-live="polite">
            <Loader2 size={14} strokeWidth={1.5} className={c.spinner} />
            <span>{formatProgress}</span>
          </div>
        )}

        {isUploading && (
          <div className={c.attachmentNotice}>
            Reading your file — you can keep chatting; until it&apos;s ready, answers will note it hasn&apos;t been searched yet.
          </div>
        )}

        <div className={c.composerCard}>
          {attachedFiles.length > 0 && (
            <div className={c.attachmentChips}>
              {attachedFiles.map((file) => (
                <div
                  key={file.id}
                  className={`${c.chip} ${
                    file.status === "error" ? c.chipError : ""
                  } ${
                    (file.status === "error" && file.error) || file.warning
                      ? c.chipWithMessage
                      : ""
                  }`}
                  title={file.status === "error" ? file.error : file.warning}
                >
                  {file.status === "uploading" && (
                    <Loader2
                      size={14}
                      strokeWidth={1.5}
                      className={`${c.chipIcon} ${c.spinner}`}
                    />
                  )}
                  {file.status === "ready" && !file.settled && (
                    <Check
                      size={14}
                      strokeWidth={1.5}
                      className={`${c.chipIcon} ${c.chipIconOk}`}
                    />
                  )}
                  {file.status === "ready" && file.settled && (
                    <FileIcon size={14} strokeWidth={1.5} className={c.chipIcon} />
                  )}
                  {file.status === "error" && (
                    <AlertCircle
                      size={14}
                      strokeWidth={1.5}
                      className={`${c.chipIcon} ${c.chipIconError}`}
                    />
                  )}
                  {/* Diagram chips only: the stored PNG as a thumbnail once
                      ready. Click opens the full-size image in a new tab. */}
                  {file.sourceType === "diagram" &&
                    file.status === "ready" &&
                    file.fileId && (
                      <a
                        className={c.chipThumbLink}
                        href={API_ENDPOINTS.fileContent(file.fileId)}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`View ${file.name} full size`}
                      >
                        <img
                          className={c.chipThumb}
                          src={API_ENDPOINTS.fileContent(file.fileId)}
                          alt={`Diagram ${file.name}`}
                          loading="lazy"
                        />
                      </a>
                    )}
                  <span className={c.chipText}>
                    <span
                      className={c.chipName}
                      title={file.status === "error" ? file.error : file.name}
                    >
                      {file.name}
                    </span>
                    {file.status === "uploading" && (
                      <span className={c.chipStage}>{stageLabel(file.stage)}</span>
                    )}
                    {/* Say what went wrong IN the chip. A tooltip alone left a
                        failed upload looking like a red filename with no reason. */}
                    {file.status === "error" && file.error && (
                      <span className={c.chipErrorText}>{file.error}</span>
                    )}
                    {/* Indexed, but part of the document was unreadable. */}
                    {file.status === "ready" && file.warning && (
                      <span className={c.chipWarningText}>{file.warning}</span>
                    )}
                  </span>
                  <button
                    type="button"
                    className={c.chipRemove}
                    onClick={() => removeAttachedFile(file.id)}
                    aria-label={`Remove ${file.name}`}
                  >
                    <X size={14} strokeWidth={1.5} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <textarea
            ref={textareaRef}
            className={c.input}
            value={userInput}
            onChange={(e) => setUserInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about the literature, or your own documents…"
            rows={1}
          />

          <div className={c.controlsRow}>
            <div className={c.controlsLeft}>
              {diagramEditorEnabled ? (
                /* Flag ON: "+" opens a two-option menu. The flag-off branch
                   below is the pre-diagram button. */
                <div className={c.attachMenuWrap}>
                  <button
                    type="button"
                    className={c.iconBtn}
                    onClick={() => setShowAttachMenu((open) => !open)}
                    title={`Attach files (${uploadTypes.label}) or draw a diagram`}
                    aria-label="Add an attachment"
                    aria-expanded={showAttachMenu}
                    aria-haspopup="menu"
                  >
                    <Plus size={16} strokeWidth={1.5} />
                  </button>
                  {showAttachMenu && (
                    <>
                      {/* Transparent backdrop: any click outside dismisses. */}
                      <div
                        className={c.attachMenuBackdrop}
                        onClick={() => setShowAttachMenu(() => false)}
                      />
                      <div className={c.attachMenu} role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          className={c.attachMenuItem}
                          onClick={() => {
                            setShowAttachMenu(() => false);
                            fileInputRef.current?.click();
                          }}
                        >
                          <FileIcon size={14} strokeWidth={1.5} />
                          Upload document
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className={c.attachMenuItem}
                          onClick={() => {
                            setShowAttachMenu(() => false);
                            setShowDiagramEditor(true);
                          }}
                        >
                          <PenLine size={14} strokeWidth={1.5} />
                          Draw a diagram
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <button
                  type="button"
                  className={c.iconBtn}
                  onClick={() => fileInputRef.current?.click()}
                  title={`Attach files (${uploadTypes.label})`}
                  aria-label="Attach files"
                >
                  <Plus size={16} strokeWidth={1.5} />
                </button>
              )}
              {/* Retrieval scope is decided by the backend router, not the
                  user — no endpoint to wire, so this renders disabled. */}
              <span title="Search-scope control is not available yet — the assistant decides retrieval per question">
                <button type="button" className={c.scopeBtn} disabled>
                  <Database size={13} strokeWidth={1.5} />
                  Knowledge base
                </button>
              </span>
            </div>
            <div className={c.controlsRight}>
              <span className={c.kbdHint} aria-hidden="true">
                ⏎ send · ⇧⏎ newline
              </span>
              <button
                type="submit"
                className={c.sendBtn}
                disabled={sendDisabled}
                aria-label="Send"
                title={
                  isUploading
                    ? "A document is still processing - you can ask now; the answer will say it was not searched yet."
                    : sendDisabled && !inputDisabled
                      ? "Type a question first"
                      : undefined
                }
              >
                <ArrowUp size={16} strokeWidth={1.5} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </form>
  );
};

export default Composer;
