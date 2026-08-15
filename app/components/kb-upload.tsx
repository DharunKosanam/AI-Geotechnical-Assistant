"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  UploadCloud,
  Check,
  AlertTriangle,
  Loader2,
  Trash2,
  FileText,
  RotateCcw,
} from "lucide-react";
import { API_ENDPOINTS } from "../config/api";
import styles from "./kb-upload.module.css";
import { toast } from "./toaster";

const ACCEPTED = ".pdf,.docx,.txt,.md,.pptx,.xlsx,.csv";
const DOC_TYPES = ["paper", "report", "thesis", "book", "standard", "slides", "dataset", "other"];
const SKIP_LABELS: Record<string, string> = {
  duplicate: "already in the KB",
  scanned: "scanned / no text",
  unsupported: "unsupported type",
  pii: "possible student/phone data",
  too_large: "too large",
};

type Warning = { kind: string; message: string; would_delete_chunks?: number };
type BulkFileStatus = { filename: string; status: string; reason?: string; canonicalTitle?: string; chunkCount?: number };
type Phase =
  | "idle" | "reading" | "form" | "submitting" | "indexing" | "done" | "error"
  | "bulkform" | "bulkindexing" | "bulkdone";

const KbUpload = () => {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [acked, setAcked] = useState<Set<string>>(new Set());
  const [extraction, setExtraction] = useState<any>(null);

  // metadata form
  const [project, setProject] = useState("");
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("");
  const [year, setYear] = useState("");
  const [authors, setAuthors] = useState("");
  const [publication, setPublication] = useState("");
  const [permission, setPermission] = useState(false);

  // result + list
  const [result, setResult] = useState<any>(null);
  const [uploads, setUploads] = useState<any[]>([]);

  // bulk (multi-file) mode
  const [bulkFiles, setBulkFiles] = useState<File[]>([]);
  const [bulkBatch, setBulkBatch] = useState<any>(null); // {status,total,done,files[]}

  const fileRef = useRef<HTMLInputElement | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadUploads = async () => {
    try {
      const r = await fetch(API_ENDPOINTS.kbMyUploads(), { credentials: "include" });
      if (r.ok) setUploads((await r.json()).uploads || []);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    loadUploads();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    setFile(null);
    setPhase("idle");
    setError("");
    setWarnings([]);
    setAcked(new Set());
    setExtraction(null);
    setProject("");
    setTitle("");
    setDocType("");
    setYear("");
    setAuthors("");
    setPublication("");
    setPermission(false);
    setResult(null);
    setBulkFiles([]);
    setBulkBatch(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const applyPrefill = (body: any) => {
    const m = body.prefilled_metadata || {};
    if (m.title) setTitle((t) => t || m.title);
    if (m.docType) setDocType((d) => d || m.docType);
    if (m.year) setYear((y) => y || String(m.year));
    if (m.authors && m.authors.length) setAuthors((a) => a || m.authors.join("; "));
    if (m.publication) setPublication((p) => p || m.publication);
    setWarnings(body.warnings || []);
    setExtraction(body.extraction || null);
  };

  // First call: file only -> extraction + prefill + warnings.
  const onPick = async (f: File) => {
    setFile(f);
    setPhase("reading");
    setError("");
    const data = new FormData();
    data.append("file", f);
    try {
      const r = await fetch(API_ENDPOINTS.kbUpload(), { method: "POST", credentials: "include", body: data });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(body.detail || `Upload failed (${r.status})`);
        setPhase("error");
        return;
      }
      applyPrefill(body);
      setPhase("form");
    } catch (e: any) {
      setError(e?.message || "Upload failed");
      setPhase("error");
    }
  };

  const allAcked = warnings.every((w) => acked.has(w.kind));
  const canSubmit =
    !!file && !!project.trim() && !!docType.trim() && !!year.trim() && permission && allAcked && phase !== "submitting";
  const bulkCanSubmit =
    bulkFiles.length > 0 && !!project.trim() && !!docType.trim() && !!year.trim() && permission && phase !== "submitting";

  const onSubmit = async () => {
    if (!file || !canSubmit) return;
    setPhase("submitting");
    setError("");
    const data = new FormData();
    data.append("file", file);
    data.append("project", project.trim());
    data.append("docType", docType.trim());
    data.append("year", year.trim());
    data.append("permissionConfirmed", permission ? "true" : "false");
    data.append("title", title.trim());
    data.append("authors", authors.trim());
    data.append("publication", publication.trim());
    data.append("acknowledge", Array.from(acked).join(","));
    try {
      const r = await fetch(API_ENDPOINTS.kbUpload(), { method: "POST", credentials: "include", body: data });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(body.detail || `Upload failed (${r.status})`);
        setPhase("error");
        return;
      }
      if (body.status === "needs_input") {
        // A further gate to acknowledge (e.g. supersede). Show it and wait.
        setWarnings(body.warnings || []);
        setPhase("form");
        return;
      }
      if (body.status === "indexing") {
        startPoll(body.batchId);
        setPhase("indexing");
        return;
      }
      setError("Unexpected server response.");
      setPhase("error");
    } catch (e: any) {
      setError(e?.message || "Upload failed");
      setPhase("error");
    }
  };

  const startPoll = (batchId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await fetch(API_ENDPOINTS.kbBatchStatus(batchId), { credentials: "include" });
        if (!r.ok) return;
        const d = await r.json();
        if (d.status === "indexed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setResult(d);
          setPhase("done");
          loadUploads();
        } else if (d.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setError(d.error || "Indexing failed.");
          setPhase("error");
        }
      } catch {
        /* keep polling */
      }
    };
    tick();
    pollRef.current = setInterval(tick, 1500);
  };

  const toggleAck = (kind: string) =>
    setAcked((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });

  const onDelete = async (batchId: string) => {
    try {
      const r = await fetch(API_ENDPOINTS.kbBatchStatus(batchId), { method: "DELETE", credentials: "include" });
      if (r.ok) loadUploads();
      else {
        const d = await r.json().catch(() => ({}));
        toast(d.detail || "Could not delete this upload.");
      }
    } catch {
      /* ignore */
    }
  };

  // --- bulk (multi-file) handlers ------------------------------------------
  const startBulk = (files: File[]) => {
    setBulkFiles(files);
    setError("");
    setPhase("bulkform");
  };

  const bulkSubmit = async () => {
    if (!bulkCanSubmit) return;
    setPhase("submitting");
    setError("");
    const data = new FormData();
    bulkFiles.forEach((f) => data.append("files", f));
    data.append("project", project.trim());
    data.append("docType", docType.trim());
    data.append("year", year.trim());
    data.append("permissionConfirmed", permission ? "true" : "false");
    try {
      const r = await fetch(API_ENDPOINTS.kbBulkUpload(), { method: "POST", credentials: "include", body: data });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(body.detail || `Upload failed (${r.status})`);
        setPhase("error");
        return;
      }
      setBulkBatch({
        status: "processing", total: body.total, done: 0,
        files: bulkFiles.map((f) => ({ filename: f.name, status: "queued" })),
      });
      startBulkPoll(body.batchId);
      setPhase("bulkindexing");
    } catch (e: any) {
      setError(e?.message || "Upload failed");
      setPhase("error");
    }
  };

  const startBulkPoll = (batchId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await fetch(API_ENDPOINTS.kbBatchStatus(batchId), { credentials: "include" });
        if (!r.ok) return;
        const d = await r.json();
        setBulkBatch(d);
        if (d.status === "done") {
          if (pollRef.current) clearInterval(pollRef.current);
          setPhase("bulkdone");
          loadUploads();
        }
      } catch {
        /* keep polling */
      }
    };
    tick();
    pollRef.current = setInterval(tick, 2000);
  };

  // Re-submit just the sensitive-PII-skipped files with a batch acknowledgement.
  const resubmitPii = async () => {
    const toResubmit: File[] = (bulkBatch?.files || [])
      .map((f: BulkFileStatus, i: number) => (f.status === "skipped" && f.reason === "pii" ? bulkFiles[i] : null))
      .filter((x: File | null): x is File => !!x);
    if (toResubmit.length === 0) return;
    setPhase("submitting");
    setError("");
    const data = new FormData();
    toResubmit.forEach((f) => data.append("files", f));
    data.append("project", project.trim());
    data.append("docType", docType.trim());
    data.append("year", year.trim());
    data.append("permissionConfirmed", permission ? "true" : "false");
    data.append("acknowledge", "pii");
    try {
      const r = await fetch(API_ENDPOINTS.kbBulkUpload(), { method: "POST", credentials: "include", body: data });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(body.detail || `Upload failed (${r.status})`);
        setPhase("error");
        return;
      }
      setBulkFiles(toResubmit);
      setBulkBatch({
        status: "processing", total: body.total, done: 0,
        files: toResubmit.map((f) => ({ filename: f.name, status: "queued" })),
      });
      startBulkPoll(body.batchId);
      setPhase("bulkindexing");
    } catch (e: any) {
      setError(e?.message || "Upload failed");
      setPhase("error");
    }
  };

  return (
    <div className={styles.container}>
      <div className={styles.panel}>
        <h1 className={styles.heading}>Contribute to the Knowledge Base</h1>
        <p className={styles.sub}>
          Add a document to the shared library. Accepted: PDF, DOCX, TXT, MD, PPTX, XLSX, CSV.
        </p>

        {(phase === "idle" || phase === "error") && !file && (
          <button type="button" className={styles.dropzone} onClick={() => fileRef.current?.click()}>
            <UploadCloud size={28} />
            <span>Choose file(s)</span>
          </button>
        )}

        <input
          ref={fileRef}
          type="file"
          accept={ACCEPTED}
          multiple
          className={styles.hidden}
          onChange={(e) => {
            const list = Array.from(e.target.files || []);
            e.target.value = "";
            if (list.length === 1) onPick(list[0]);
            else if (list.length > 1) startBulk(list);
          }}
        />

        {phase === "reading" && (
          <div className={styles.status}>
            <Loader2 size={18} className={styles.spin} /> Reading &amp; checking your file…
          </div>
        )}

        {phase === "form" && (
          <>
            {extraction && (
              <div className={styles.extraction}>
                <FileText size={15} /> {file?.name} · {extraction.source_format?.toUpperCase()} ·{" "}
                {extraction.pages} page(s)
              </div>
            )}

            {warnings.length > 0 && (
              <div className={styles.warnings}>
                {warnings.map((w) => (
                  <label key={w.kind} className={styles.warning}>
                    <input type="checkbox" checked={acked.has(w.kind)} onChange={() => toggleAck(w.kind)} />
                    <AlertTriangle size={15} className={styles.warnIcon} />
                    <span>{w.message}</span>
                  </label>
                ))}
              </div>
            )}

            <div className={styles.form}>
              <label className={styles.field}>
                <span>Project <em>*</em></span>
                <input value={project} onChange={(e) => setProject(e.target.value)} placeholder="e.g. Site B foundations" />
              </label>
              <label className={styles.field}>
                <span>Title</span>
                <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Canonical title (used in citations)" />
              </label>
              <div className={styles.row}>
                <label className={styles.field}>
                  <span>Type <em>*</em></span>
                  <select value={docType} onChange={(e) => setDocType(e.target.value)}>
                    <option value="">Select…</option>
                    {DOC_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={styles.field}>
                  <span>Year <em>*</em></span>
                  <input value={year} onChange={(e) => setYear(e.target.value)} inputMode="numeric" placeholder="2024" />
                </label>
              </div>
              <label className={styles.field}>
                <span>Authors</span>
                <input value={authors} onChange={(e) => setAuthors(e.target.value)} placeholder="Semicolon-separated" />
              </label>
              <label className={styles.field}>
                <span>Publication</span>
                <input value={publication} onChange={(e) => setPublication(e.target.value)} placeholder="Journal / conference / publisher" />
              </label>
              <label className={styles.check}>
                <input type="checkbox" checked={permission} onChange={(e) => setPermission(e.target.checked)} />
                <span>I confirm I have permission to add this document to the shared knowledge base. <em>*</em></span>
              </label>
            </div>

            <div className={styles.actions}>
              <button type="button" className={styles.secondary} onClick={reset}>
                Cancel
              </button>
              <button type="button" className={styles.primary} disabled={!canSubmit} onClick={onSubmit}>
                Add to Knowledge Base
              </button>
            </div>
          </>
        )}

        {(phase === "submitting" || phase === "indexing") && (
          <div className={styles.status}>
            <Loader2 size={18} className={styles.spin} />{" "}
            {phase === "submitting" ? "Submitting…" : "Indexing your document…"}
          </div>
        )}

        {phase === "done" && result && (
          <div className={styles.result}>
            <div className={styles.resultHead}>
              <Check size={18} strokeWidth={1.5} className={styles.okIcon} /> Added to the knowledge base
            </div>
            <div className={styles.resultBody}>
              <div>
                <strong>{result.canonicalTitle}</strong> · v{result.version} · {result.chunkCount} chunk(s) ·{" "}
                {result.sourceFormat?.toUpperCase()}
              </div>
              {result.sampleChunk && <pre className={styles.sample}>{result.sampleChunk}</pre>}
            </div>
            <button type="button" className={styles.primary} onClick={reset}>
              <RotateCcw size={15} /> Upload another
            </button>
          </div>
        )}

        {phase === "bulkform" && (
          <>
            <div className={styles.extraction}>
              <FileText size={15} /> {bulkFiles.length} files selected — one project &amp; permission for the whole batch.
            </div>
            <ul className={styles.list}>
              {bulkFiles.map((f, i) => (
                <li key={i} className={styles.listItem}>
                  <span className={styles.listTitle}>{f.name}</span>
                  <span className={styles.listMeta}>{(f.size / 1024).toFixed(0)} KB</span>
                </li>
              ))}
            </ul>
            <p className={styles.sub}>Each file&apos;s title, authors and year are extracted per file during indexing.</p>
            <div className={styles.form}>
              <label className={styles.field}>
                <span>Project <em>*</em></span>
                <input value={project} onChange={(e) => setProject(e.target.value)} placeholder="e.g. Site B foundations" />
              </label>
              <div className={styles.row}>
                <label className={styles.field}>
                  <span>Type <em>*</em></span>
                  <select value={docType} onChange={(e) => setDocType(e.target.value)}>
                    <option value="">Select…</option>
                    {DOC_TYPES.map((t) => (<option key={t} value={t}>{t}</option>))}
                  </select>
                </label>
                <label className={styles.field}>
                  <span>Year <em>*</em></span>
                  <input value={year} onChange={(e) => setYear(e.target.value)} inputMode="numeric" placeholder="2024" />
                </label>
              </div>
              <label className={styles.check}>
                <input type="checkbox" checked={permission} onChange={(e) => setPermission(e.target.checked)} />
                <span>I confirm I have permission to add these documents to the shared knowledge base. <em>*</em></span>
              </label>
            </div>
            <div className={styles.actions}>
              <button type="button" className={styles.secondary} onClick={reset}>Cancel</button>
              <button type="button" className={styles.primary} disabled={!bulkCanSubmit} onClick={bulkSubmit}>
                Add {bulkFiles.length} files to Knowledge Base
              </button>
            </div>
          </>
        )}

        {(phase === "bulkindexing" || phase === "bulkdone") && bulkBatch && (
          <div className={styles.result}>
            <div className={styles.resultHead}>
              {phase === "bulkindexing" ? (
                <Loader2 size={18} className={styles.spin} />
              ) : (
                <Check size={18} strokeWidth={1.5} className={styles.okIcon} />
              )}{" "}
              Indexing {bulkBatch.done ?? 0}/{bulkBatch.total ?? bulkFiles.length}
              {phase === "bulkdone" ? " — done" : "…"}
            </div>
            <ul className={styles.list}>
              {(bulkBatch.files || []).map((f: BulkFileStatus, i: number) => (
                <li key={i} className={styles.listItem}>
                  <div className={styles.listMain}>
                    <span className={styles.listTitle}>{f.canonicalTitle || f.filename}</span>
                    <span className={styles.listMeta}>
                      {f.status === "done" && `indexed · ${f.chunkCount ?? "?"} chunks`}
                      {f.status === "processing" && "processing…"}
                      {f.status === "queued" && "queued"}
                      {f.status === "skipped" && `skipped · ${SKIP_LABELS[f.reason || ""] || f.reason}`}
                      {f.status === "failed" && "failed"}
                    </span>
                  </div>
                  {f.status === "done" && <Check size={15} strokeWidth={1.5} className={styles.okIcon} />}
                  {f.status === "processing" && <Loader2 size={15} className={styles.spin} />}
                  {(f.status === "skipped" || f.status === "failed") && (
                    <AlertTriangle size={15} className={styles.warnIcon} />
                  )}
                </li>
              ))}
            </ul>
            {phase === "bulkindexing" ? (
              <p className={styles.sub}>You can close this tab — indexing continues, and your uploads appear below.</p>
            ) : (
              <div className={styles.actions}>
                {(bulkBatch.files || []).some((f: BulkFileStatus) => f.status === "skipped" && f.reason === "pii") && (
                  <button type="button" className={styles.secondary} onClick={resubmitPii}>
                    Add {(bulkBatch.files || []).filter((f: BulkFileStatus) => f.status === "skipped" && f.reason === "pii").length} flagged file(s) anyway
                  </button>
                )}
                <button type="button" className={styles.primary} onClick={reset}>
                  <RotateCcw size={15} /> Upload more
                </button>
              </div>
            )}
          </div>
        )}

        {phase === "error" && (
          <div className={styles.errorBox}>
            <AlertTriangle size={16} /> {error}
            <button type="button" className={styles.secondary} onClick={reset}>
              Try again
            </button>
          </div>
        )}
      </div>

      <div className={styles.panel}>
        <h2 className={styles.heading2}>Your recent uploads</h2>
        {uploads.length === 0 ? (
          <p className={styles.sub}>Nothing yet.</p>
        ) : (
          <ul className={styles.list}>
            {uploads.map((u) => (
              <li key={u.batchId} className={styles.listItem}>
                <div className={styles.listMain}>
                  <span className={styles.listTitle}>{u.canonicalTitle || u.filename}</span>
                  <span className={styles.listMeta}>
                    {u.projectTag} · v{u.version} ·{" "}
                    {u.status === "indexed" ? `${u.chunkCount ?? "?"} chunks` : u.status}
                  </span>
                </div>
                {u.deletable ? (
                  <button
                    type="button"
                    className={styles.deleteBtn}
                    onClick={() => onDelete(u.batchId)}
                    aria-label="Delete upload"
                    title="Delete this upload"
                  >
                    <Trash2 size={15} />
                  </button>
                ) : (
                  <span className={styles.locked} title="Past the self-delete window">
                    locked
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

export default KbUpload;
