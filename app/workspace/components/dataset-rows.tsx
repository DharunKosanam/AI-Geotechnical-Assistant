"use client";

import React, { useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Gauge,
  Loader2,
  RotateCw,
  Waves,
  X,
} from "lucide-react";

import styles from "../workspace.module.css";
import { ACTIVE_STATES, statusLine, type DatasetRecord, type Segment } from "../instruments";

/**
 * One dataset row in the GeoPilot session panel (INSTRUMENT_PARSERS_ENABLED).
 *
 * Shows what was detected (compact badge), the parse progress ON THE ROW
 * (bar + percentage -- parsing outlives the user's attention span for the
 * thread), a failed state with the error and a retry affordance, and an
 * expander that lists the dataset's segments (events) as children.
 */
export function DatasetRow({
  ds,
  onRemove,
  onRetry,
}: {
  ds: DatasetRecord;
  onRemove: (ds: DatasetRecord) => void;
  onRetry: (ds: DatasetRecord) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = ACTIVE_STATES.includes(ds.status);
  const failed = ds.status === "failed";
  const segments: Segment[] = ds.segments ?? [];
  const canExpand = segments.length > 0;
  const Icon = ds.dataset_kind === "pressure_timeseries" ? Gauge : Waves;

  return (
    <div className={styles.dsItem} data-status={ds.status}>
      <div className={styles.docRow} title={failed ? ds.error ?? undefined : ds.filename}>
        <button
          type="button"
          className={styles.dsExpand}
          onClick={() => canExpand && setOpen((o) => !o)}
          aria-label={
            canExpand
              ? `${open ? "Collapse" : "Expand"} ${segments.length} segments`
              : "No segments"
          }
          aria-expanded={canExpand ? open : undefined}
          disabled={!canExpand}
          tabIndex={canExpand ? 0 : -1}
        >
          {canExpand ? (
            open ? <ChevronDown size={14} /> : <ChevronRight size={14} />
          ) : (
            <span className={styles.dsExpandSpacer} />
          )}
        </button>
        <span className={styles.docIcon}>
          {active ? (
            <Loader2 size={14} className={styles.spin} />
          ) : failed ? (
            <AlertTriangle size={14} strokeWidth={1.5} className={styles.docErrorIcon} />
          ) : (
            <Icon size={14} strokeWidth={1.5} />
          )}
        </span>
        <span className={styles.dsText}>
          <span className={styles.docName}>{ds.filename}</span>
          {active ? (
            <span className={styles.dsProgressLine} aria-live="polite">
              <span className={styles.dsProgressTrack} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(ds.progress || 0)}>
                <span
                  className={styles.dsProgressFill}
                  style={{ width: `${Math.max(2, Math.min(100, ds.progress || 0))}%` }}
                />
              </span>
              <span className={styles.dsProgressPct}>{statusLine(ds)}</span>
            </span>
          ) : failed ? (
            <span className={styles.dsError}>{ds.error || "Parsing failed."}</span>
          ) : (
            <span className={styles.dsBadge}>
              {ds.badge}
              {canExpand ? ` · ${segments.length} events` : ""}
            </span>
          )}
        </span>
        {failed && (
          <button
            type="button"
            className={styles.dsRetry}
            onClick={() => onRetry(ds)}
            aria-label={`Retry parsing ${ds.filename}`}
            title="Retry parsing"
          >
            <RotateCw size={14} />
          </button>
        )}
        <button
          type="button"
          className={styles.docRemove}
          onClick={() => onRemove(ds)}
          aria-label={`Remove ${ds.filename}`}
        >
          <X size={14} />
        </button>
      </div>
      {open && canExpand && (
        <ul className={styles.dsSegments} aria-label={`Segments of ${ds.filename}`}>
          {segments.map((s) => (
            <li key={s.index} className={styles.dsSegment} title={s.start ? `${s.start} → ${s.end ?? ""}` : undefined}>
              <span className={styles.dsSegmentLabel}>{s.label}</span>
              <span className={styles.dsSegmentMeta}>
                {typeof s.peak_sum_kpa === "number" ? `${s.peak_sum_kpa.toFixed(1)} kPa` : ""}
                {typeof s.duration_s === "number" ? ` · ${s.duration_s.toFixed(1)} s` : ""}
                {s.direction ? ` · ${s.direction}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
