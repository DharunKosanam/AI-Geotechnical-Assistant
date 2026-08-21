"use client";

/**
 * Add a web page to the knowledge base by pasted URL (WEB_INGEST_ENABLED).
 *
 * Self-gating: renders NOTHING unless /api/kb/status carries webIngest: true
 * (the key exists only when the backend flag is on), so with the flag off the
 * KB surface is exactly today's. Flow: paste URL -> fetch preview (title +
 * first lines + warnings, nothing indexed) -> confirm -> ingest. A URL
 * already in the KB is offered as a Refresh (supersede) instead.
 *
 * Every fetch failure arrives as {code, message} from the backend and renders
 * its own specific heading + message — never a generic failure.
 */
import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Globe,
  Link2,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { API_ENDPOINTS } from "../config/api";
import styles from "./kb-upload.module.css";

type Phase = "idle" | "previewing" | "preview" | "ingesting" | "done" | "error";

type Preview = {
  resolvedUrl: string;
  title: string;
  preview: string;
  charCount: number;
  textRatio: number;
  ingestable: boolean;
  warnings: string[];
  alreadyIngested: { canonicalTitle?: string; fetchedAt?: string; version?: number } | null;
};

type IngestResult = {
  batchId: string;
  canonicalUrl: string;
  canonicalTitle: string;
  fetchedAt: string;
  previousFetchedAt: string | null;
  contentChanged: boolean;
  version: number;
  chunkCount: number;
  superseded: number;
};

// Specific heading per backend error code — the message body comes from the
// backend, which already names hosts/types/limits precisely.
const ERROR_TITLES: Record<string, string> = {
  not_allowlisted: "This site is not on the allowed list",
  private_address: "Internal addresses cannot be fetched",
  login_wall: "This page is behind a sign-in wall",
  wrong_content_type: "Not a web page",
  timeout: "The page timed out",
  too_large: "The page is too large",
  too_many_redirects: "Too many redirects",
  dns_failure: "Host not found",
  http_error: "The page returned an error",
  fetch_error: "The page could not be fetched",
  bad_scheme: "Only http(s) links are supported",
  bad_port: "Non-standard port",
  invalid_url: "That does not look like a URL",
  already_ingested: "Already in the knowledge base",
  duplicate_content: "Duplicate content",
  no_usable_text: "No readable text on this page",
  missing_project: "Project is required",
  permission_not_confirmed: "Confirmation required",
};

const fmtDate = (iso?: string | null) =>
  iso ? iso.slice(0, 10) : "unknown date";

const KbWebIngest = ({ onIngested }: { onIngested?: () => void }) => {
  const [enabled, setEnabled] = useState(false);
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [project, setProject] = useState("");
  const [permission, setPermission] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [errCode, setErrCode] = useState("");
  const [errMessage, setErrMessage] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(API_ENDPOINTS.kbStatus(), { credentials: "include" });
        if (!r.ok) return;
        const d = await r.json();
        if (alive && d.webIngest === true) setEnabled(true);
      } catch {
        /* stay hidden */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!enabled) return null;

  const fail = (code: string, message: string) => {
    setErrCode(code);
    setErrMessage(message);
    setPhase("error");
  };

  const readError = async (r: Response) => {
    const body = await r.json().catch(() => ({} as any));
    const d = body?.detail;
    if (d && typeof d === "object") return fail(d.code || "", d.message || "Request failed.");
    return fail("", typeof d === "string" ? d : `Request failed (${r.status}).`);
  };

  const reset = () => {
    setUrl("");
    setPhase("idle");
    setPreview(null);
    setProject("");
    setPermission(false);
    setResult(null);
    setErrCode("");
    setErrMessage("");
  };

  const onPreview = async () => {
    if (!url.trim()) return;
    setPhase("previewing");
    try {
      const r = await fetch(API_ENDPOINTS.kbWebPreview(), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      if (!r.ok) return void (await readError(r));
      setPreview(await r.json());
      setPhase("preview");
    } catch (e: any) {
      fail("fetch_error", e?.message || "Could not reach the server.");
    }
  };

  const refreshing = !!preview?.alreadyIngested;
  const canIngest =
    !!preview?.ingestable && !!project.trim() && permission && phase === "preview";

  const onIngest = async () => {
    if (!preview || !canIngest) return;
    setPhase("ingesting");
    try {
      const r = await fetch(API_ENDPOINTS.kbWebIngest(), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: preview.resolvedUrl,
          project: project.trim(),
          permissionConfirmed: permission,
          refresh: refreshing,
        }),
      });
      if (!r.ok) return void (await readError(r));
      setResult(await r.json());
      setPhase("done");
      onIngested?.();
    } catch (e: any) {
      fail("fetch_error", e?.message || "Could not reach the server.");
    }
  };

  return (
    <div className={styles.webSection}>
      <h2 className={styles.heading2}>
        <Globe size={16} /> Add a web page by link
      </h2>
      <p className={styles.sub}>
        Paste a public UVic (or approved) page — funding, awards, deadlines. The
        page is captured as it is today; refresh it when it changes.
      </p>

      {(phase === "idle" || phase === "previewing") && (
        <div className={styles.urlRow}>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onPreview();
            }}
            placeholder="https://www.uvic.ca/…"
            aria-label="Web page URL"
            disabled={phase === "previewing"}
          />
          <button
            type="button"
            className={styles.primary}
            disabled={!url.trim() || phase === "previewing"}
            onClick={onPreview}
          >
            {phase === "previewing" ? (
              <>
                <Loader2 size={15} className={styles.spin} /> Fetching…
              </>
            ) : (
              <>
                <Link2 size={15} /> Fetch preview
              </>
            )}
          </button>
        </div>
      )}

      {phase === "preview" && preview && (
        <>
          <div className={styles.extraction}>
            <Globe size={15} /> {preview.title || preview.resolvedUrl} ·{" "}
            {preview.charCount.toLocaleString()} characters
          </div>
          <div className={styles.resultBody}>
            <span className={styles.listMeta}>{preview.resolvedUrl}</span>
            <pre className={styles.sample}>{preview.preview}</pre>
          </div>

          {preview.warnings.map((w, i) => (
            <div key={i} className={styles.warning}>
              <AlertTriangle size={15} className={styles.warnIcon} />
              <span>{w}</span>
            </div>
          ))}

          {refreshing && (
            <div className={styles.warning}>
              <AlertTriangle size={15} className={styles.warnIcon} />
              <span>
                This page is already in the knowledge base (fetched{" "}
                {fmtDate(preview.alreadyIngested?.fetchedAt)}). Adding it again
                will replace the stored copy with today&apos;s version.
              </span>
            </div>
          )}

          <div className={styles.form}>
            <label className={styles.field}>
              <span>
                Project <em>*</em>
              </span>
              <input
                value={project}
                onChange={(e) => setProject(e.target.value)}
                placeholder="e.g. uvic-funding"
              />
            </label>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={permission}
                onChange={(e) => setPermission(e.target.checked)}
              />
              <span>
                I confirm this is a public page whose content may be stored in
                the shared knowledge base. <em>*</em>
              </span>
            </label>
          </div>

          <div className={styles.actions}>
            <button type="button" className={styles.secondary} onClick={reset}>
              Cancel
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={!canIngest}
              onClick={onIngest}
            >
              {refreshing ? "Refresh in Knowledge Base" : "Add to Knowledge Base"}
            </button>
          </div>
        </>
      )}

      {phase === "ingesting" && (
        <div className={styles.status}>
          <Loader2 size={18} className={styles.spin} /> Fetching and indexing the
          page…
        </div>
      )}

      {phase === "done" && result && (
        <div className={styles.result}>
          <div className={styles.resultHead}>
            <Check size={18} strokeWidth={1.5} className={styles.okIcon} />{" "}
            {result.superseded > 0 ? "Page refreshed" : "Page added to the knowledge base"}
          </div>
          <div className={styles.resultBody}>
            <div>
              <strong>{result.canonicalTitle}</strong> · v{result.version} ·{" "}
              {result.chunkCount} chunk(s)
            </div>
            <span className={styles.listMeta}>
              fetched {fmtDate(result.fetchedAt)}
              {result.previousFetchedAt &&
                ` · replaced copy from ${fmtDate(result.previousFetchedAt)}`}
              {result.previousFetchedAt && !result.contentChanged &&
                " · content unchanged"}
            </span>
          </div>
          <button type="button" className={styles.primary} onClick={reset}>
            <RotateCcw size={15} /> Add another page
          </button>
        </div>
      )}

      {phase === "error" && (
        <div className={styles.errorBox}>
          <AlertTriangle size={16} />
          <span>
            <strong>{ERROR_TITLES[errCode] || "Something went wrong"}.</strong>{" "}
            {errMessage}
          </span>
          <button type="button" className={styles.secondary} onClick={reset}>
            Try again
          </button>
        </div>
      )}
    </div>
  );
};

export default KbWebIngest;
