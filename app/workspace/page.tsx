"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  FileText,
  X,
  Loader2,
  Sparkles,
  Plus,
  AlertTriangle,
  Database,
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
import { toast } from "../components/toaster";
import { DatasetRow } from "./components/dataset-rows";
import { DatasetMessage } from "./components/dataset-cards";
import { LineChart, type ChartPayload } from "./components/strain-chart";
import {
  ACTIVE_STATES,
  type DatasetRecord,
  type ParseJob,
  type Segment,
} from "./instruments";

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
  // Dataset-bound calculators (INSTRUMENT_PARSERS_ENABLED) -- all optional so
  // CPT results and re-opened history runs are unaffected.
  summary?: Record<string, unknown>;
  charts?: ChartPayload[];
  notices?: { level: string; text: string }[];
  dataset_id?: string;
  dataset_kind?: string;
  segments?: Segment[];
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
    headline?: string;
    dataset_kind?: string;
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
  | { id: string; role: "assistant"; kind: "result"; data: ResultPayload }
  // Instrument dataset upload (INSTRUMENT_PARSERS_ENABLED): ONE message per
  // dataset, rendered from the live dataset record so it updates in place
  // (progress -> summary card / error) instead of appending per poll.
  | { id: string; role: "assistant"; kind: "dataset"; datasetId: string };

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

  // Instrument datasets (INSTRUMENT_PARSERS_ENABLED). The backend is the single
  // source of truth: /api/workspace/status carries `instrument_parsers: true`
  // ONLY when the flag is on; absent = off, and this page renders exactly as
  // before (flat document list, .cpt-only picker, no Datasets group).
  const [instrumentEnabled, setInstrumentEnabled] = useState(false);
  const [instrumentExtensions, setInstrumentExtensions] = useState<string[]>([]);
  const [datasets, setDatasets] = useState<DatasetRecord[]>([]);
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

  // --- Instrument datasets --------------------------------------------------
  const loadDatasets = async () => {
    try {
      const res = await fetch("/api/workspace/datasets", { credentials: "include" });
      if (res.ok) setDatasets(((await res.json()).datasets ?? []) as DatasetRecord[]);
    } catch {
      /* best-effort; the list stays as-is */
    }
  };

  useEffect(() => {
    let active = true;
    fetch("/api/workspace/status", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : {}))
      .then((d: any) => {
        if (!active) return;
        const on = d?.instrument_parsers === true;
        setInstrumentEnabled(on);
        setInstrumentExtensions(
          on && Array.isArray(d?.instrument_extensions) ? d.instrument_extensions : [],
        );
        if (on) loadDatasets();
      })
      .catch(() => {
        if (active) setInstrumentEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Poll the parse job of every queued/parsing dataset once a second; when a
  // job reaches a terminal state, refetch that dataset (full metadata) so the
  // row and its thread message update in place.
  const activeIds = datasets
    .filter((d) => ACTIVE_STATES.includes(d.status))
    .map((d) => d.id)
    .join(",");
  useEffect(() => {
    if (!instrumentEnabled || !activeIds) return;
    let cancelled = false;
    const tick = async () => {
      const current = datasets.filter((d) => ACTIVE_STATES.includes(d.status));
      await Promise.all(
        current.map(async (d) => {
          if (!d.job_id) return;
          try {
            const res = await fetch(`/api/workspace/datasets/jobs/${d.job_id}`, {
              credentials: "include",
            });
            if (!res.ok || cancelled) return;
            const job = (await res.json()) as ParseJob;
            if (ACTIVE_STATES.includes(job.state)) {
              setDatasets((prev) =>
                prev.map((x) =>
                  x.id === d.id ? { ...x, status: job.state, progress: job.progress } : x,
                ),
              );
              return;
            }
            const full = await fetch(`/api/workspace/datasets/${d.id}`, {
              credentials: "include",
            });
            if (cancelled) return;
            if (full.ok) {
              const rec = (await full.json()) as DatasetRecord;
              setDatasets((prev) => prev.map((x) => (x.id === d.id ? rec : x)));
            } else {
              setDatasets((prev) =>
                prev.map((x) =>
                  x.id === d.id
                    ? { ...x, status: job.state, progress: job.progress, error: job.error }
                    : x,
                ),
              );
            }
          } catch {
            /* transient; next tick retries */
          }
        }),
      );
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrumentEnabled, activeIds]);

  const removeDataset = (ds: DatasetRecord) => {
    setDatasets((prev) => prev.filter((d) => d.id !== ds.id));
    setMessages((prev) =>
      prev.filter((m) => !(m.role === "assistant" && m.kind === "dataset" && m.datasetId === ds.id)),
    );
    fetch(`/api/workspace/datasets/${ds.id}`, { method: "DELETE", credentials: "include" }).catch(
      () => {},
    );
  };

  const retryDataset = async (ds: DatasetRecord) => {
    try {
      const res = await fetch(`/api/workspace/datasets/${ds.id}/retry`, {
        method: "POST",
        credentials: "include",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || `Retry failed (HTTP ${res.status}).`);
      setDatasets((prev) => prev.map((d) => (d.id === ds.id ? (data as DatasetRecord) : d)));
    } catch (e: any) {
      toast(e?.message ?? "Retry failed.");
    }
  };

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
        summary_text: ro.dataset_id
          ? `Re-opened run — ${run.summary?.headline ?? ro.calculator_name ?? "dataset result"}`
          : `Re-opened run — ${(ro.layers?.length ?? 0)} layer(s).`,
        layers: ro.layers ?? [],
        metadata: ro.metadata ?? {},
        interpretation: null,
        run_id: run.id,
        // Export only when the persisted run actually declares a table.
        exportable: (ro.tables?.length ?? 0) > 0,
        // Dataset-bound runs persist their deterministic block + charts.
        summary: ro.summary,
        charts: ro.charts,
        notices: ro.notices,
        dataset_id: ro.dataset_id,
        dataset_kind: ro.dataset_kind,
      };
      setMessages((prev) => [
        ...prev,
        { id: uid(), role: "assistant", kind: "result", data },
      ]);
    } catch (e: any) {
      toast(e?.message ?? "Could not open run.");
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
      toast(e?.message ?? "Could not open thread.");
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
      // With the instrument capability on, uploads go through the streaming
      // Route Handler (instrument files are 22-58 MB; the rewrite caps bodies at
      // 10 MB). It forwards to the SAME backend handler. Flag off: unchanged.
      const res = await fetch(
        instrumentEnabled ? "/api/workspace/upload" : "/api/workspace/documents",
        {
          method: "POST",
          body: form,
          credentials: "include",
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          (data && (data.detail as string)) ||
            `Upload failed (HTTP ${res.status}).`,
        );
      }
      if (instrumentEnabled && data && data.kind === "dataset") {
        // The backend sniffed an instrument file: it is a DATASET (numeric,
        // computed on), not a document. Move it out of the document list into
        // the Datasets group and start its one in-place thread message.
        const rec: DatasetRecord = {
          id: data.dataset_id || data.id,
          kind: "dataset",
          filename: data.filename,
          size_bytes: data.size_bytes,
          parser_id: data.parser_id,
          dataset_kind: data.dataset_kind,
          label: data.label,
          badge: data.badge || data.label,
          status: data.status || "queued",
          progress: data.progress || 0,
          error: null,
          job_id: data.job_id,
          metadata: {},
          warnings: [],
          segments: [],
        };
        setDocs((prev) => prev.filter((d) => d.id !== tempId));
        setDatasets((prev) => [rec, ...prev.filter((d) => d.id !== rec.id)]);
        setMessages((prev) => [
          ...prev,
          { id: uid(), role: "assistant", kind: "dataset", datasetId: rec.id },
        ]);
        return;
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
        // A dataset-bound run may have detected segments/events: they become
        // children of that dataset's row in the panel.
        if (data.dataset_id && Array.isArray(data.segments)) {
          setDatasets((prev) =>
            prev.map((d) =>
              d.id === data.dataset_id ? { ...d, segments: data.segments } : d,
            ),
          );
        }
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
      toast(e?.message ?? "Export failed.");
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
                {instrumentEnabled && (
                  <div className={styles.panelGroup}>
                    <FileText size={12} strokeWidth={1.5} /> Documents
                    <span className={styles.panelGroupCount}>{docs.length || ""}</span>
                  </div>
                )}
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
                          <AlertTriangle size={14} strokeWidth={1.5} className={styles.docErrorIcon} />
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
                {instrumentEnabled && (
                  <>
                    <div className={styles.panelGroup}>
                      <Database size={12} strokeWidth={1.5} /> Datasets
                      <span className={styles.panelGroupCount}>{datasets.length || ""}</span>
                    </div>
                    {datasets.length === 0 ? (
                      <p className={styles.docEmpty}>
                        No datasets yet. Upload an instrument file (ODiSI .tsv, Campbell
                        .dat) — it is parsed into a numeric dataset, not embedded.
                      </p>
                    ) : (
                      datasets.map((ds) => (
                        <DatasetRow
                          key={ds.id}
                          ds={ds}
                          onRemove={removeDataset}
                          onRetry={retryDataset}
                        />
                      ))
                    )}
                  </>
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
                          {fmtDate(r.created_at)} ·{" "}
                          {r.summary?.headline
                            ? r.summary.headline
                            : `${r.summary?.layer_count ?? 0} layers`}
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
                {instrumentEnabled && (
                  <p>
                    Instrument files (ODiSI strain <code>.tsv</code>, Campbell
                    pressure <code>.dat</code>) are parsed into datasets; a
                    calculator only runs when you ask for it.
                  </p>
                )}
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
                if (m.kind === "dataset") {
                  const ds = datasets.find((d) => d.id === m.datasetId);
                  if (!ds) return null;
                  return (
                    <div key={m.id} className={styles.messageRow}>
                      <DatasetMessage ds={ds} />
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
              accept={
                instrumentEnabled && instrumentExtensions.length > 0
                  ? [FILE_ACCEPT, ...instrumentExtensions].join(",")
                  : FILE_ACCEPT
              }
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

// Deterministic-block rows for a dataset-bound result: the calculator's
// exportable ``summary`` (the same key/values that go to the Excel Summary
// sheet), minus the reference which is rendered on its own line.
function detRows(summary: Record<string, unknown> | undefined): [string, string][] {
  if (!summary) return [];
  const rows: [string, string][] = [];
  for (const [label, value] of Object.entries(summary)) {
    if (label === "Method / Standard reference") continue;
    let text: string;
    if (value == null) text = "—";
    else if (typeof value === "number") text = Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
    else text = String(value);
    rows.push([label, text]);
  }
  return rows;
}

function ResultCard({
  data,
  onExport,
}: {
  data: ResultPayload;
  onExport: (runId: string, sourceFile: string) => void;
}) {
  const interp = data.interpretation;
  const isDataset = Boolean(data.dataset_id) || (data.layers.length === 0 && Boolean(data.summary));
  // Dataset results: the AI draft is collapsed behind an explicit review
  // affordance. CPT results keep today's expanded section.
  const [showDraft, setShowDraft] = useState(false);
  const notices = data.notices ?? [];
  return (
    <div className={styles.resultCard}>
      <div className={styles.resultLead}>
        <FlaskConical size={16} /> Ran <b>{data.calculator_name}</b> on{" "}
        <b>{data.source_file}</b>
      </div>
      {data.summary_text && (
        <p className={styles.resultSummary}>{data.summary_text}</p>
      )}

      {isDataset && (
        <>
          {/* Status notices render INSIDE the deterministic block, visibly --
              e.g. a threshold method pending engineering validation. */}
          {notices.map((n, i) => (
            <div
              key={i}
              className={`${styles.notice} ${
                n.level === "provisional"
                  ? styles.noticeProvisional
                  : n.level === "warning"
                    ? styles.noticeWarning
                    : styles.noticeInfo
              }`}
              role={n.level === "info" ? undefined : "note"}
            >
              <AlertTriangle size={14} />
              {n.level === "provisional" && <span className={styles.noticeTag}>Provisional</span>}
              <span>{n.text}</span>
            </div>
          ))}
          <div className={styles.detBlock}>
            {detRows(data.summary).map(([label, value]) => (
              <div key={label} className={styles.detRow}>
                <span className={styles.detLabel}>{label}</span>
                <span className={styles.detValue} title={value}>
                  {value}
                </span>
              </div>
            ))}
          </div>
          {(data.charts ?? []).length > 0 && (
            <div className={styles.charts}>
              {(data.charts ?? []).map((c) => (
                <LineChart key={c.id} chart={c} />
              ))}
            </div>
          )}
        </>
      )}

      {!isDataset && (
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
      )}

      <div className={styles.reference}>Reference: {data.reference}</div>

      {/* Dataset results: AI draft collapsed behind an explicit affordance. */}
      {isDataset && interp && !showDraft && (
        <button
          type="button"
          className={styles.aiToggle}
          onClick={() => setShowDraft(true)}
          aria-expanded={false}
        >
          <Sparkles size={14} /> Show AI draft interpretation — for engineer review
        </button>
      )}

      {/* AI interpretation — a separate, clearly-labelled section. Omitted for
          re-opened runs (deterministic result only, no stored AI text). */}
      {interp && (!isDataset || showDraft) && (
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
