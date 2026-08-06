"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  FileText,
  X,
  Loader2,
  Sparkles,
  Plus,
  AlertTriangle,
  Download,
  FlaskConical,
  History,
  MessageSquare,
  SquarePen,
} from "lucide-react";

import Header from "../components/Header";
import { AuthProvider } from "../lib/auth-context";
import AuthGuard from "../components/auth-guard";
import styles from "./workspace.module.css";

// Documents the calculators can draw from are .CPT soundings today. The set can
// widen as more calculators are registered.
const FILE_ACCEPT = ".cpt,.CPT";

type DocStatus = "uploading" | "ready" | "error";
type SessionDoc = {
  id: string;
  filename: string;
  status: DocStatus;
  error?: string;
};

type Layer = {
  layer: number;
  depth_from: number;
  depth_to: number;
  thickness: number;
  sbt_zone: number;
  soil_type: string;
  qc_mean: number;
  ic_mean: number;
};

type Interpretation = {
  narrative: string;
  flagged_concerns?: string[];
  is_ai_draft?: boolean;
  model?: string;
  error?: string;
};

type ResultPayload = {
  type: "result";
  calculator_id: string;
  calculator_name: string;
  source_file: string;
  reference: string;
  params: Record<string, number>;
  summary_text: string;
  layers: Layer[];
  metadata: Record<string, unknown>;
  interpretation: Interpretation | null;
  result_id?: string;
  run_id?: string | null;
  exportable: boolean;
};

// History (durable, per-user).
type RunSummary = {
  id: string;
  source_filename: string;
  created_at: string;
  calculator_id: string;
  summary: {
    layer_count?: number;
    max_depth?: number;
    gwl?: number;
    area_ratio?: number;
    reference?: string;
  };
};

type ThreadSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

type StoredMessage = {
  role: "user" | "assistant";
  type: string;
  content: any;
  is_ai_draft?: boolean;
};

type LeftTab = "documents" | "history";

type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; kind: "text"; text: string }
  | { id: string; role: "assistant"; kind: "answer"; text: string; isDraft: boolean }
  | { id: string; role: "assistant"; kind: "result"; data: ResultPayload };

const uid = (): string =>
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.round(Math.random() * 1e6)}`;

function num(v: unknown, digits = 2): string {
  return typeof v === "number" ? v.toFixed(digits) : "—";
}

function fmtDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// Map a persisted thread message back into a renderable chat message.
function fromStored(m: StoredMessage): ChatMessage {
  if (m.role === "user") return { id: uid(), role: "user", text: String(m.content ?? "") };
  if (m.type === "result") {
    return { id: uid(), role: "assistant", kind: "result", data: m.content as ResultPayload };
  }
  if (m.type === "answer") {
    return {
      id: uid(),
      role: "assistant",
      kind: "answer",
      text: String(m.content ?? ""),
      isDraft: m.is_ai_draft !== false,
    };
  }
  return { id: uid(), role: "assistant", kind: "text", text: String(m.content ?? "") };
}

function GeoPilot() {
  const [docs, setDocs] = useState<SessionDoc[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  // Left panel: Documents | History sub-tabs.
  const [leftTab, setLeftTab] = useState<LeftTab>("documents");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  // The active History thread this session's messages are appended to.
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);

  // Keep the thread pinned to the newest message.
  useEffect(() => {
    const el = messagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // --- History ---------------------------------------------------------------
  const loadHistory = async () => {
    try {
      const [runsRes, threadsRes] = await Promise.all([
        fetch("/api/workspace/history/runs", { credentials: "include" }),
        fetch("/api/workspace/history/threads", { credentials: "include" }),
      ]);
      if (runsRes.ok) setRuns((await runsRes.json()).runs ?? []);
      if (threadsRes.ok) setThreads((await threadsRes.json()).threads ?? []);
    } catch {
      /* history is best-effort; leave lists as-is on failure */
    }
  };

  useEffect(() => {
    loadHistory();
  }, []);

  // Re-open a past run as a fresh result card (its Export button re-fetches by id).
  const openRun = async (runId: string) => {
    try {
      const res = await fetch(`/api/workspace/history/runs/${runId}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`Could not open run (HTTP ${res.status}).`);
      const run = await res.json();
      const ro = run.result_object ?? {};
      const data: ResultPayload = {
        type: "result",
        calculator_id: ro.calculator_id,
        calculator_name: ro.calculator_name ?? "CPT interpretation",
        source_file: ro.source_file ?? run.source_filename,
        reference: ro.reference ?? "",
        params: {},
        summary_text: `Re-opened run — ${(ro.layers?.length ?? 0)} layer(s).`,
        layers: ro.layers ?? [],
        metadata: ro.metadata ?? {},
        interpretation: null,
        run_id: run.id,
        // Export only when the persisted run actually declares a table.
        exportable: (ro.tables?.length ?? 0) > 0,
      };
      setMessages((prev) => [
        ...prev,
        { id: uid(), role: "assistant", kind: "result", data },
      ]);
    } catch (e: any) {
      alert(e?.message ?? "Could not open run.");
    }
  };

  // Load a past thread's messages into the chat area and make it active.
  const openThread = async (threadId: string) => {
    try {
      const res = await fetch(`/api/workspace/history/threads/${threadId}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(`Could not open thread (HTTP ${res.status}).`);
      const thread = await res.json();
      setMessages((thread.messages ?? []).map(fromStored));
      setActiveThreadId(threadId);
    } catch (e: any) {
      alert(e?.message ?? "Could not open thread.");
    }
  };

  const openPicker = () => fileInputRef.current?.click();

  // Return GeoPilot to the empty welcome state: clear the thread, the document
  // list, and the active result. History (past runs/threads in Mongo) is NOT
  // deleted -- it stays viewable under the History tab.
  const newSession = () => {
    // Best-effort: also drop this session's server-side documents so a fresh
    // board really is clean (a later run won't silently reuse an old doc).
    docs.forEach((d) => {
      if (d.status === "ready") {
        fetch(`/api/workspace/documents/${d.id}`, {
          method: "DELETE",
          credentials: "include",
        }).catch(() => {});
      }
    });
    setDocs([]);
    setMessages([]);
    setInput("");
    setActiveThreadId(null);
    setLeftTab("documents");
  };

  // --- Document panel --------------------------------------------------------
  const uploadDoc = async (tempId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch("/api/workspace/documents", {
        method: "POST",
        body: form,
        credentials: "include",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          (data && (data.detail as string)) ||
            `Upload failed (HTTP ${res.status}).`,
        );
      }
      // Swap the temp row for the server record (real id, ready status).
      setDocs((prev) =>
        prev.map((d) =>
          d.id === tempId
            ? { id: data.id, filename: data.filename, status: "ready" }
            : d,
        ),
      );
    } catch (e: any) {
      setDocs((prev) =>
        prev.map((d) =>
          d.id === tempId
            ? { ...d, status: "error", error: e?.message ?? "Upload failed" }
            : d,
        ),
      );
    }
  };

  const handleFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (!selected || selected.length === 0) return;
    const files = Array.from(selected);
    e.target.value = ""; // allow re-selecting the same file
    for (const file of files) {
      const tempId = uid();
      setDocs((prev) => [
        ...prev,
        { id: tempId, filename: file.name, status: "uploading" },
      ]);
      uploadDoc(tempId, file);
    }
  };

  const removeDoc = (doc: SessionDoc) => {
    setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    // Only "ready" rows have a real server-side id worth deleting.
    if (doc.status === "ready") {
      fetch(`/api/workspace/documents/${doc.id}`, {
        method: "DELETE",
        credentials: "include",
      }).catch(() => {
        /* best-effort; the row is already gone from the UI */
      });
    }
  };

  // --- Chat ------------------------------------------------------------------
  const appendAssistantText = (text: string) =>
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: "assistant", kind: "text", text },
    ]);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setMessages((prev) => [...prev, { id: uid(), role: "user", text }]);
    setInput("");
    setSending(true);

    try {
      const res = await fetch("/api/workspace/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ message: text, thread_id: activeThreadId }),
      });
      const data = await res.json().catch(() => ({}));
      // Track the thread the backend recorded this turn in.
      if (data && data.thread_id) setActiveThreadId(data.thread_id);
      if (!res.ok) {
        appendAssistantText(
          (data && (data.detail as string)) ||
            `Request failed (HTTP ${res.status}).`,
        );
      } else if (data.type === "result") {
        setMessages((prev) => [
          ...prev,
          { id: uid(), role: "assistant", kind: "result", data },
        ]);
      } else if (data.type === "answer") {
        setMessages((prev) => [
          ...prev,
          {
            id: uid(),
            role: "assistant",
            kind: "answer",
            text: data.answer || "",
            isDraft: data.is_ai_draft !== false,
          },
        ]);
      } else {
        appendAssistantText(data.text || "");
      }
    } catch (err: any) {
      appendAssistantText(err?.message ?? "Something went wrong.");
    } finally {
      setSending(false);
      // A run may have created a new run/thread — refresh History so it shows.
      loadHistory();
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sending && input.trim()) send(e as any);
    }
  };

  // --- Excel export ----------------------------------------------------------
  // Exports by durable run id (survives restart); the backend rebuilds the
  // workbook from the persisted deterministic result object.
  const downloadExcel = async (runId: string, sourceFile: string) => {
    try {
      const res = await fetch(`/api/workspace/export/${runId}`, {
        credentials: "include",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Export failed (HTTP ${res.status}).`);
      }
      const blob = await res.blob();
      const cd = res.headers.get("content-disposition") || "";
      const match = cd.match(/filename="?([^"]+)"?/);
      const filename = match?.[1] || `CPT_${sourceFile}.xlsx`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      alert(e?.message ?? "Export failed.");
    }
  };

  return (
    <main className={styles.main}>
      <Header />
      <div className={styles.workspace}>
        {/* Left: session documents + durable history */}
        <aside className={styles.docPanel}>
          <button
            type="button"
            className={styles.newSessionBtn}
            onClick={newSession}
          >
            <SquarePen size={15} /> New session
          </button>
          <div className={styles.leftTabs} role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={leftTab === "documents"}
              className={`${styles.leftTab} ${leftTab === "documents" ? styles.leftTabActive : ""}`}
              onClick={() => setLeftTab("documents")}
            >
              <FileText size={14} /> Documents
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={leftTab === "history"}
              className={`${styles.leftTab} ${leftTab === "history" ? styles.leftTabActive : ""}`}
              onClick={() => {
                setLeftTab("history");
                loadHistory();
              }}
            >
              <History size={14} /> History
            </button>
          </div>

          {leftTab === "documents" ? (
            <>
              <div className={styles.docList}>
                {docs.length === 0 ? (
                  <p className={styles.docEmpty}>
                    No documents yet. Upload a .CPT sounding to get started.
                  </p>
                ) : (
                  docs.map((doc) => (
                    <div key={doc.id} className={styles.docRow}>
                      <span className={styles.docIcon}>
                        {doc.status === "uploading" ? (
                          <Loader2 size={14} className={styles.spin} />
                        ) : doc.status === "error" ? (
                          <AlertTriangle size={14} color="#b91c1c" />
                        ) : (
                          <FileText size={14} />
                        )}
                      </span>
                      <span
                        className={styles.docName}
                        title={doc.status === "error" ? doc.error : doc.filename}
                      >
                        {doc.filename}
                      </span>
                      <button
                        type="button"
                        className={styles.docRemove}
                        onClick={() => removeDoc(doc)}
                        aria-label={`Remove ${doc.filename}`}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </>
          ) : (
            <div className={styles.docList}>
              {runs.length === 0 && threads.length === 0 ? (
                <p className={styles.docEmpty}>
                  No history yet. Run a CPT to get started.
                </p>
              ) : (
                <>
                  {runs.length > 0 && (
                    <div className={styles.historyGroup}>Runs</div>
                  )}
                  {runs.map((r) => (
                    <button
                      key={r.id}
                      type="button"
                      className={styles.historyRow}
                      onClick={() => openRun(r.id)}
                      title={`Re-open ${r.source_filename}`}
                    >
                      <FlaskConical size={13} className={styles.historyIcon} />
                      <span className={styles.historyText}>
                        <span className={styles.historyName}>{r.source_filename}</span>
                        <span className={styles.historyMeta}>
                          {fmtDate(r.created_at)} · {r.summary?.layer_count ?? 0} layers
                        </span>
                      </span>
                    </button>
                  ))}

                  {threads.length > 0 && (
                    <div className={styles.historyGroup}>Threads</div>
                  )}
                  {threads.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className={styles.historyRow}
                      onClick={() => openThread(t.id)}
                      title={`Open ${t.title}`}
                    >
                      <MessageSquare size={13} className={styles.historyIcon} />
                      <span className={styles.historyText}>
                        <span className={styles.historyName}>{t.title}</span>
                        <span className={styles.historyMeta}>{fmtDate(t.updated_at)}</span>
                      </span>
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
        </aside>

        {/* Right: chat thread + input */}
        <section className={styles.chatArea}>
          <div className={styles.messages} ref={messagesRef}>
            {messages.length === 0 ? (
              <div className={styles.welcome}>
                <FlaskConical size={22} />
                <h2>GeoPilot workspace</h2>
                <p>
                  Upload a .CPT sounding on the left, then type{" "}
                  <code>run CPT</code> to interpret it. Add parameters inline,
                  e.g. <code>run CPT, groundwater 2m, unit weight 18</code>.
                </p>
              </div>
            ) : (
              messages.map((m) => {
                if (m.role === "user") {
                  return (
                    <div
                      key={m.id}
                      className={`${styles.messageRow} ${styles.rowRight}`}
                    >
                      <div className={styles.userMessage}>{m.text}</div>
                    </div>
                  );
                }
                if (m.kind === "result") {
                  return (
                    <div key={m.id} className={styles.messageRow}>
                      <ResultCard data={m.data} onExport={downloadExcel} />
                    </div>
                  );
                }
                if (m.kind === "answer") {
                  return (
                    <div key={m.id} className={styles.messageRow}>
                      <AnswerMessage text={m.text} isDraft={m.isDraft} />
                    </div>
                  );
                }
                return (
                  <div key={m.id} className={styles.messageRow}>
                    <div className={styles.assistantMessage}>{m.text}</div>
                  </div>
                );
              })
            )}
          </div>

          <form className={styles.inputForm} onSubmit={send}>
            <input
              type="file"
              ref={fileInputRef}
              className={styles.hiddenInput}
              accept={FILE_ACCEPT}
              multiple
              onChange={handleFiles}
            />
            <div className={styles.inputRow}>
              <button
                type="button"
                className={styles.plusBtn}
                onClick={openPicker}
                title="Upload a document"
                aria-label="Upload a document"
              >
                <Plus size={20} />
              </button>
              <textarea
                className={styles.input}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Message GeoPilot (e.g. run CPT) — Shift+Enter for a new line"
                rows={1}
              />
              <button
                type="submit"
                className={styles.button}
                disabled={sending || !input.trim()}
              >
                {sending ? <Loader2 size={16} className={styles.spin} /> : "Send"}
              </button>
            </div>
          </form>
        </section>
      </div>
    </main>
  );
}

function AnswerMessage({ text, isDraft }: { text: string; isDraft: boolean }) {
  return (
    <div className={styles.answerMessage}>
      {isDraft && (
        <div className={styles.answerHeader}>
          <Sparkles size={14} />
          <span className={styles.draftBadge}>AI draft — for engineer review</span>
        </div>
      )}
      {text
        .split(/\n{2,}/)
        .filter((p) => p.trim())
        .map((para, i) => (
          <p key={i} className={styles.narrative}>
            {para.trim()}
          </p>
        ))}
    </div>
  );
}

function ResultCard({
  data,
  onExport,
}: {
  data: ResultPayload;
  onExport: (runId: string, sourceFile: string) => void;
}) {
  const interp = data.interpretation;
  return (
    <div className={styles.resultCard}>
      <div className={styles.resultLead}>
        <FlaskConical size={16} /> Ran <b>{data.calculator_name}</b> on{" "}
        <b>{data.source_file}</b>
      </div>
      {data.summary_text && (
        <p className={styles.resultSummary}>{data.summary_text}</p>
      )}

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Layer</th>
              <th>Depth (m)</th>
              <th>Thickness (m)</th>
              <th>Soil behaviour type</th>
              <th>Mean qc (MPa)</th>
              <th>Mean Ic</th>
            </tr>
          </thead>
          <tbody>
            {data.layers.map((ly) => (
              <tr key={ly.layer}>
                <td>{ly.layer}</td>
                <td>
                  {num(ly.depth_from)}–{num(ly.depth_to)}
                </td>
                <td>{num(ly.thickness)}</td>
                <td>
                  {ly.soil_type}
                  <span className={styles.zoneTag}>zone {ly.sbt_zone}</span>
                </td>
                <td>{num(ly.qc_mean)}</td>
                <td>{num(ly.ic_mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={styles.reference}>Reference: {data.reference}</div>

      {/* AI interpretation — a separate, clearly-labelled section. Omitted for
          re-opened runs (deterministic result only, no stored AI text). */}
      {interp && (
        <div className={styles.aiSection}>
          <div className={styles.aiHeader}>
            <h4 className={styles.sectionTitle}>
              <Sparkles size={15} /> AI interpretation
            </h4>
            {interp.is_ai_draft && (
              <span className={styles.draftBadge}>AI draft — for engineer review</span>
            )}
          </div>

          {interp.error ? (
            <div className={styles.error}>
              <AlertTriangle size={15} /> {interp.error}
            </div>
          ) : (
            (interp.narrative ?? "")
              .split(/\n{2,}/)
              .filter((p) => p.trim())
              .map((para, i) => (
                <p key={i} className={styles.narrative}>
                  {para.trim()}
                </p>
              ))
          )}

          {interp.flagged_concerns && interp.flagged_concerns.length > 0 && (
            <div className={styles.concerns}>
              <div className={styles.concernsTitle}>
                <AlertTriangle size={14} /> Flagged for review
              </div>
              <ul>
                {interp.flagged_concerns.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {data.exportable && data.run_id && (
        <button
          type="button"
          className={styles.exportBtn}
          onClick={() => onExport(data.run_id!, data.source_file)}
        >
          <Download size={15} /> Export to Excel
        </button>
      )}
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <AuthProvider>
      <AuthGuard>
        <GeoPilot />
      </AuthGuard>
    </AuthProvider>
  );
}
