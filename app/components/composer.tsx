"use client";

/**
 * Composer + attachment surfaces. Extracted verbatim from chat.tsx (the
 * <form> region) so the dark-redesign restyle diffs cleanly against a pure
 * move; every handler and piece of state stays owned by chat.tsx and arrives
 * as props under its original name.
 */
import React from "react";
import DiagramEditorModal from "./diagram-editor-modal";
import { API_ENDPOINTS } from "../config/api";
import { Plus, X, File as FileIcon, Loader2, Check, AlertCircle, PenLine } from "lucide-react";
import styles from "./chat.module.css";

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
  return (
      <form
        onSubmit={handleSubmit}
        className={`${styles.inputForm} ${styles.clearfix}`}
      >
        {showJoinModal && (
          <div className={styles.modal}>
            <div className={styles.modalContent}>
              <h3>Join Team Chat</h3>
              <input
                type="text"
                value={joinThreadInput}
                onChange={(e) => setJoinThreadInput(e.target.value)}
                placeholder="Enter Team Thread ID"
              />
              <div className={styles.modalButtons}>
                <button onClick={() => setShowJoinModal(false)}>Cancel</button>
                <button onClick={handleJoinTeam}>Join</button>
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
              alert(message);
            }}
          />
        )}
        {/* Hidden file input — kept in the DOM, triggered by the "+" button.
            Same picker the old "Attach files" button used. */}
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          accept={uploadTypes.extensions.join(",")}
          multiple
          onChange={handleFileAttach}
        />
        <div className={styles.inputContainer}>
          {attachedFiles.length > 0 && (
            <div className={styles.attachmentChips}>
              {attachedFiles.map((file) => (
                <div
                  key={file.id}
                  className={`${styles.chip} ${
                    file.status === "error" ? styles.chipError : ""
                  } ${
                    (file.status === "error" && file.error) || file.warning
                      ? styles.chipWithMessage
                      : ""
                  }`}
                  title={file.status === "error" ? file.error : file.warning}
                >
                  {file.status === "uploading" && (
                    <Loader2
                      size={16}
                      className={`${styles.chipIcon} ${styles.spinner}`}
                    />
                  )}
                  {file.status === "ready" && !file.settled && (
                    <Check size={16} color="#4ade80" className={styles.chipIcon} />
                  )}
                  {file.status === "ready" && file.settled && (
                    <FileIcon size={14} className={styles.chipIcon} />
                  )}
                  {file.status === "error" && (
                    <AlertCircle size={16} color="#f87171" className={styles.chipIcon} />
                  )}
                  {/* Diagram chips only: the stored PNG as a thumbnail once
                      ready, served through the /api/files beforeFiles rewrite.
                      Click opens the full-size image in a new tab. Purely
                      additive — document chips (no sourceType) are untouched,
                      and the pending/ready/failed lifecycle is unchanged. */}
                  {file.sourceType === "diagram" &&
                    file.status === "ready" &&
                    file.fileId && (
                      <a
                        className={styles.chipThumbLink}
                        href={API_ENDPOINTS.fileContent(file.fileId)}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`View ${file.name} full size`}
                      >
                        <img
                          className={styles.chipThumb}
                          src={API_ENDPOINTS.fileContent(file.fileId)}
                          alt={`Diagram ${file.name}`}
                          loading="lazy"
                        />
                      </a>
                    )}
                  <span className={styles.chipText}>
                    <span
                      className={styles.chipName}
                      title={file.status === "error" ? file.error : file.name}
                    >
                      {file.name}
                    </span>
                    {file.status === "uploading" && (
                      <span className={styles.chipStage}>
                        {stageLabel(file.stage)}
                      </span>
                    )}
                    {/* Say what went wrong IN the chip. A tooltip alone left a
                        failed upload looking like a red filename with no reason. */}
                    {file.status === "error" && file.error && (
                      <span className={styles.chipErrorText}>{file.error}</span>
                    )}
                    {/* Indexed, but part of the document was unreadable. */}
                    {file.status === "ready" && file.warning && (
                      <span className={styles.chipWarningText}>{file.warning}</span>
                    )}
                  </span>
                  <button
                    type="button"
                    className={styles.chipRemove}
                    onClick={() => removeAttachedFile(file.id)}
                    aria-label={`Remove ${file.name}`}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {isUploading && (
            <div className={styles.attachmentNotice}>
              Reading your file — you can keep chatting; until it&apos;s ready, answers will note it hasn&apos;t been searched yet.
            </div>
          )}
          {sourceSetsEnabled && threadId && (
            <div className={styles.sourcesBar}>
              <button
                type="button"
                className={styles.sourcesToggle}
                onClick={() => setShowSources((s) => !s)}
              >
                {showSources ? "Hide sources" : `Sources (${threadSources?.length ?? 0})`}
              </button>
              {showSources && (
                <div className={styles.sourcesPanel}>
                  {(threadSources ?? []).length === 0 && (
                    <div className={styles.sourcesEmpty}>
                      No sources in this conversation yet. Attach a file to add one.
                    </div>
                  )}
                  {(threadSources ?? []).map((s) => (
                    <div key={s.filename} className={styles.sourceRow}>
                      <span className={styles.sourceName} title={s.filename}>
                        {s.filename}
                      </span>
                      <span className={styles.sourceMeta}>
                        {s.status === "ready"
                          ? `${s.chunkCount} sections`
                          : s.status === "pending"
                            ? "processing..."
                            : `failed${s.reason ? `: ${s.reason}` : ""}`}
                        {" - "}
                        {s.provenance === "vision"
                          ? "AI vision-derived"
                          : s.provenance === "mixed"
                            ? `text + AI vision (pages ${s.visionPages.join(", ")})`
                            : "verbatim text"}
                        {s.partiallyIndexed && s.warning ? ` - ${s.warning}` : ""}
                      </span>
                      {removePreview?.filename === s.filename ? (
                        <span className={styles.sourceConfirm}>
                          Delete {removePreview.chunksToDelete} sections and{" "}
                          {removePreview.parentDocsToDelete} document record? Other
                          conversations and the knowledge base are not affected.
                          <button
                            type="button"
                            className={styles.sourceConfirmBtn}
                            disabled={removeBusy}
                            onClick={confirmRemoveSource}
                          >
                            Delete
                          </button>
                          <button
                            type="button"
                            className={styles.sourceCancelBtn}
                            disabled={removeBusy}
                            onClick={() => setRemovePreview(null)}
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          className={styles.sourceRemoveBtn}
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
            <div className={styles.formatButtons}>
              <span className={styles.formatButtonsLabel}>Generate from sources:</span>
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
                  <span key={f.key} className={styles.formatBtnWrap} title={tooltip}>
                    <button
                      type="button"
                      className={styles.formatBtn}
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
            <div className={styles.formatProgressNotice}>
              <Loader2 size={16} className={styles.spinner} />
              <span>{formatProgress}</span>
            </div>
          )}
          <div className={styles.inputRow}>
            {diagramEditorEnabled ? (
              /* Flag ON: "+" opens a two-option menu. The flag-off branch below
                 is the pre-diagram button, character-identical. */
              <div className={styles.attachMenuWrap}>
                <button
                  type="button"
                  className={styles.plusBtn}
                  onClick={() => setShowAttachMenu((open) => !open)}
                  title={`Attach files (${uploadTypes.label}) or draw a diagram`}
                  aria-label="Add an attachment"
                  aria-expanded={showAttachMenu}
                  aria-haspopup="menu"
                >
                  <Plus size={20} />
                </button>
                {showAttachMenu && (
                  <>
                    {/* Transparent backdrop: any click outside dismisses. */}
                    <div
                      className={styles.attachMenuBackdrop}
                      onClick={() => setShowAttachMenu(false)}
                    />
                    <div className={styles.attachMenu} role="menu">
                      <button
                        type="button"
                        role="menuitem"
                        className={styles.attachMenuItem}
                        onClick={() => {
                          setShowAttachMenu(false);
                          fileInputRef.current?.click();
                        }}
                      >
                        <FileIcon size={16} />
                        Upload document
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        className={styles.attachMenuItem}
                        onClick={() => {
                          setShowAttachMenu(false);
                          setShowDiagramEditor(true);
                        }}
                      >
                        <PenLine size={16} />
                        Draw a diagram
                      </button>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <button
                type="button"
                className={styles.plusBtn}
                onClick={() => fileInputRef.current?.click()}
                title={`Attach files (${uploadTypes.label})`}
                aria-label="Attach files"
              >
                <Plus size={20} />
              </button>
            )}
            <textarea
              ref={textareaRef}
              className={styles.input}
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Enter your question (Shift+Enter for new line)"
              rows={1}
              style={{
                resize: 'vertical',
                minHeight: '40px',
                maxHeight: '200px',
                overflow: 'auto',
              }}
            />
            <button
              type="submit"
              className={styles.button}
              disabled={inputDisabled}
              title={isUploading ? "A document is still processing - you can ask now; the answer will say it was not searched yet." : undefined}
            >
              Send
            </button>
          </div>
        </div>
      </form>
  );
};

export default Composer;
