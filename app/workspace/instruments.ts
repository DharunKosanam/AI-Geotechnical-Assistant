/**
 * Instrument datasets (INSTRUMENT_PARSERS_ENABLED) -- shared types + pure
 * helpers for the GeoPilot page. No React here so the helpers are unit-testable.
 */

export type DatasetStatus = "queued" | "parsing" | "parsed" | "failed";

export type Segment = {
  index: number;
  label: string;
  start?: string | null;
  end?: string | null;
  duration_s?: number | null;
  peak_sum_kpa?: number | null;
  peak_kpa?: number[] | null;
  channel_order?: string[] | null;
  direction?: string | null;
};

export type DatasetRecord = {
  id: string;
  kind: "dataset";
  filename: string;
  size_bytes?: number;
  parser_id: string;
  dataset_kind: string;
  label: string;
  badge: string;
  status: DatasetStatus;
  progress: number;
  error?: string | null;
  job_id?: string | null;
  metadata: Record<string, any>;
  shapes?: Record<string, number[]>;
  dtypes?: Record<string, string>;
  warnings?: string[];
  segments?: Segment[];
  created_at?: string | null;
  parsed_at?: string | null;
};

export type ParseJob = {
  id: string;
  dataset_id: string;
  state: DatasetStatus;
  progress: number;
  error?: string | null;
  elapsed_s?: number | null;
};

export const ACTIVE_STATES: DatasetStatus[] = ["queued", "parsing"];

/** Trigger phrase advertised for each dataset kind (explicit-trigger only;
 *  the assistant never runs a calculator on its own). */
export const CALCULATOR_HINT: Record<string, string> = {
  strain_distributed: "run dfos pass strain",
  pressure_timeseries: "run traffic load monitoring",
};

export function kindTitle(kind: string): string {
  if (kind === "strain_distributed") return "Distributed fibre-optic strain (DFOS)";
  if (kind === "pressure_timeseries") return "Pressure-cell time series";
  return kind;
}

function fmt(v: unknown, digits = 2): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function fmtInt(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toLocaleString() : "—";
}

function shortTs(v: unknown): string {
  return typeof v === "string" ? v.replace("T", " ") : "—";
}

export type MetricTile = { label: string; value: string; mono?: boolean };

/** Metric tiles for the parse-summary card, from parser metadata only. */
export function summaryTiles(ds: DatasetRecord): MetricTile[] {
  const m = ds.metadata || {};
  if (ds.dataset_kind === "strain_distributed") {
    return [
      { label: "Gages", value: fmtInt(m.n_gages) },
      { label: "Timesteps", value: fmtInt(m.n_timesteps) },
      { label: "Fibre span (m)", value: `${fmt(m.x_min_m)} → ${fmt(m.x_max_m)}` },
      { label: "Gage pitch (mm)", value: fmt(m.gage_pitch_mm, 2) },
      { label: "Rate (Hz)", value: fmt(m.sample_rate_hz ?? m.measurement_rate_hz, 3) },
      { label: "Duration (s)", value: fmt(m.duration_s, 1) },
      { label: "Tare", value: m.tare_name != null ? String(m.tare_name) : "—" },
      { label: "Units", value: m.units ? String(m.units) : "—" },
    ];
  }
  if (ds.dataset_kind === "pressure_timeseries") {
    const names: string[] = Array.isArray(m.channel_names) ? m.channel_names : [];
    const maxes: number[] = Array.isArray(m.column_max) ? m.column_max : [];
    const tiles: MetricTile[] = [
      { label: "Channels", value: fmtInt(m.n_channels) },
      { label: "Samples", value: fmtInt(m.n_samples) },
      { label: "Rate (Hz)", value: fmt(m.sample_rate_hz, 1) },
      { label: "First sample", value: shortTs(m.first_timestamp), mono: true },
      { label: "Last sample", value: shortTs(m.last_timestamp), mono: true },
      { label: "Span (h)", value: fmt(typeof m.duration_s === "number" ? m.duration_s / 3600 : undefined, 2) },
    ];
    names.forEach((n, i) => tiles.push({ label: `Max ${n}`, value: fmt(maxes[i]) }));
    return tiles;
  }
  return Object.entries(m)
    .filter(([k, v]) => !k.startsWith("_") && (typeof v === "number" || typeof v === "string"))
    .slice(0, 8)
    .map(([k, v]) => ({ label: k, value: String(v) }));
}

/** Short row subtitle under a dataset's filename while it is being parsed. */
export function statusLine(ds: DatasetRecord): string {
  if (ds.status === "queued") return "Queued for parsing…";
  if (ds.status === "parsing") return `Parsing… ${Math.round(ds.progress || 0)}%`;
  if (ds.status === "failed") return ds.error || "Parsing failed.";
  return ds.badge;
}

export function fmtBytes(n?: number): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "";
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}
