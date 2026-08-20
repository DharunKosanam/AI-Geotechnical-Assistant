"use client";

import React from "react";
import { AlertTriangle, Database, Loader2 } from "lucide-react";

import styles from "../workspace.module.css";
import {
  ACTIVE_STATES,
  CALCULATOR_HINT,
  kindTitle,
  summaryTiles,
  type DatasetRecord,
} from "../instruments";

/**
 * Thread messages for a dataset upload (INSTRUMENT_PARSERS_ENABLED).
 *
 * ONE message per dataset that updates in place: while the parse runs it is a
 * progress line; on completion the same message becomes the parse-summary
 * card (metric tiles from parser metadata + warnings + an explicit line that
 * NO calculation has run); on failure it shows the error.
 */
export function DatasetMessage({ ds }: { ds: DatasetRecord }) {
  if (ACTIVE_STATES.includes(ds.status)) {
    const pct = Math.round(ds.progress || 0);
    return (
      <div className={styles.parseProgress} role="status" aria-live="polite">
        <Loader2 size={16} className={styles.spin} />
        <span className={styles.parseProgressText}>
          {ds.status === "queued" ? "Queued" : "Parsing"} <b>{ds.filename}</b>
          {ds.status === "parsing" ? ` — ${pct}%` : ""}
        </span>
        <span className={styles.dsProgressTrack} aria-hidden="true">
          <span className={styles.dsProgressFill} style={{ width: `${Math.max(2, Math.min(100, pct))}%` }} />
        </span>
      </div>
    );
  }
  if (ds.status === "failed") {
    return (
      <div className={styles.parseFailed} role="alert">
        <AlertTriangle size={16} />
        <span>
          Could not parse <b>{ds.filename}</b>: {ds.error || "unknown error"}. Use the retry
          control on its row in the Datasets list.
        </span>
      </div>
    );
  }
  const tiles = summaryTiles(ds);
  const hint = CALCULATOR_HINT[ds.dataset_kind];
  const warnings = ds.warnings ?? [];
  return (
    <div className={styles.parseCard}>
      <div className={styles.resultLead}>
        <Database size={16} /> Parsed <b>{ds.filename}</b>
        <span className={styles.dsBadgeInline}>{ds.badge}</span>
      </div>
      <p className={styles.parseKind}>{kindTitle(ds.dataset_kind)}</p>
      <div className={styles.tiles}>
        {tiles.map((t) => (
          <div key={t.label} className={styles.tile}>
            <span className={styles.tileLabel}>{t.label}</span>
            <span className={styles.tileValue}>{t.value}</span>
          </div>
        ))}
      </div>
      {warnings.length > 0 && (
        <div className={styles.parseWarnings}>
          <div className={styles.concernsTitle}>
            <AlertTriangle size={14} /> Parser warnings
          </div>
          <ul>
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      <p className={styles.parseNoCalc}>
        No calculation has run on this dataset. This card only reports what the file
        contains.
        {hint ? (
          <>
            {" "}
            To compute, type <code>{hint}</code>.
          </>
        ) : null}
      </p>
    </div>
  );
}
