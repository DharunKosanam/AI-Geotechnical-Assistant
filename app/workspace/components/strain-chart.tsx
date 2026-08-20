"use client";

import React, { useMemo } from "react";

import styles from "../workspace.module.css";

/**
 * Minimal inline-SVG line chart for calculator result cards.
 *
 * The repo has no charting dependency and installing one is out of scope, so
 * this draws the server-side DOWNSAMPLED payload (<= 2,000 points per series,
 * see calculators/dataset_charts.py) with plain SVG: axes, ticks, one
 * polyline per series, optional point markers. Colours come from the existing
 * tokens only. Purely presentational: no state, no timers.
 */
export type ChartSeries = {
  name: string;
  x: (number | null)[];
  y: (number | null)[];
  n_source?: number;
  kind?: "line" | "bar"; // bar = one rect per point (histograms)
};
export type ChartBand = { x0: number; x1: number; label?: string };
export type ChartPayload = {
  id: string;
  title: string;
  x_label: string;
  y_label: string;
  series: ChartSeries[];
  markers?: { x: number; y: number; label?: string }[];
  // Optional axis extents wider than the data (e.g. the full fibre while only
  // the analysed span carries data) and shaded regions along x (bands) or y
  // (bands_y) with labels -- used to show trimmed fibre ends explicitly.
  x_range?: [number, number];
  y_range?: [number, number];
  bands?: ChartBand[];
  bands_y?: ChartBand[];
};

const W = 640;
const H = 220;
const PAD = { top: 12, right: 14, bottom: 34, left: 54 };
const SERIES_COLOURS = ["var(--accent)", "var(--warn)", "var(--t2)", "var(--danger)"];

function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const span = max - min;
  const rough = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const start = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

function fmtTick(v: number): string {
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3).replace(/\.?0+$/, "") || "0";
}

export function LineChart({ chart, height = H }: { chart: ChartPayload; height?: number }) {
  const model = useMemo(() => {
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (const s of chart.series) {
      for (let i = 0; i < s.x.length; i++) {
        const x = s.x[i], y = s.y[i];
        if (x == null || y == null) continue;
        if (x < xmin) xmin = x;
        if (x > xmax) xmax = x;
        if (y < ymin) ymin = y;
        if (y > ymax) ymax = y;
      }
    }
    for (const m of chart.markers ?? []) {
      if (m.y < ymin) ymin = m.y;
      if (m.y > ymax) ymax = m.y;
    }
    if (!Number.isFinite(xmin) || !Number.isFinite(ymin)) return null;
    if (chart.x_range) { xmin = Math.min(xmin, chart.x_range[0]); xmax = Math.max(xmax, chart.x_range[1]); }
    if (chart.y_range) { ymin = Math.min(ymin, chart.y_range[0]); ymax = Math.max(ymax, chart.y_range[1]); }
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    if (xmin === xmax) { xmin -= 1; xmax += 1; }
    if (chart.series.some((s) => s.kind === "bar")) {
      // Give the first/last bar room; bars sit on a zero baseline.
      const xs = chart.series[0].x.filter((v): v is number => v != null);
      const gap = xs.length > 1 ? Math.abs(xs[1] - xs[0]) : 1;
      xmin -= gap / 2; xmax += gap / 2;
      if (ymin > 0) ymin = 0;
    }
    const ypad = (ymax - ymin) * 0.06;
    ymin -= ypad; ymax += ypad;
    const iw = W - PAD.left - PAD.right;
    const ih = height - PAD.top - PAD.bottom;
    const sx = (x: number) => PAD.left + ((x - xmin) / (xmax - xmin)) * iw;
    const sy = (y: number) => PAD.top + (1 - (y - ymin) / (ymax - ymin)) * ih;
    const paths = chart.series.map((s) => {
      if (s.kind === "bar") return "";
      let d = "";
      let pen = false;
      for (let i = 0; i < s.x.length; i++) {
        const x = s.x[i], y = s.y[i];
        if (x == null || y == null) { pen = false; continue; }
        d += `${pen ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`;
        pen = true;
      }
      return d;
    });
    // Bars: width from the smallest x-gap of the series (histogram bins).
    const bars = chart.series.map((s) => {
      if (s.kind !== "bar") return [] as { x: number; y: number; w: number; h: number }[];
      const xs = s.x.filter((v): v is number => v != null);
      let gap = Infinity;
      for (let i = 1; i < xs.length; i++) gap = Math.min(gap, Math.abs(xs[i] - xs[i - 1]));
      const wPx = Number.isFinite(gap) ? Math.max(2, (gap / (xmax - xmin)) * iw * 0.8) : 6;
      const y0 = sy(Math.max(0, ymin));
      const out: { x: number; y: number; w: number; h: number }[] = [];
      for (let i = 0; i < s.x.length; i++) {
        const x = s.x[i], y = s.y[i];
        if (x == null || y == null) continue;
        const top = sy(y);
        out.push({ x: sx(x) - wPx / 2, y: Math.min(top, y0), w: wPx, h: Math.abs(y0 - top) });
      }
      return out;
    });
    return { xmin, xmax, ymin, ymax, sx, sy, paths, bars, xt: niceTicks(xmin, xmax, 6), yt: niceTicks(ymin, ymax, 5) };
  }, [chart, height]);

  if (!model) {
    return <div className={styles.chartEmpty}>No plottable data for “{chart.title}”.</div>;
  }
  const { sx, sy, paths, bars, xt, yt } = model;
  const zeroInRange = model.ymin < 0 && model.ymax > 0;
  return (
    <figure className={styles.chart}>
      <figcaption className={styles.chartTitle}>{chart.title}</figcaption>
      <svg
        viewBox={`0 0 ${W} ${height}`}
        className={styles.chartSvg}
        role="img"
        aria-label={`${chart.title}: ${chart.series.map((s) => s.name).join(", ")}`}
      >
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(v)} y2={sy(v)} className={styles.chartGrid} />
            <text x={PAD.left - 6} y={sy(v)} className={styles.chartTick} textAnchor="end" dominantBaseline="middle">
              {fmtTick(v)}
            </text>
          </g>
        ))}
        {xt.map((v) => (
          <g key={`x${v}`}>
            <line x1={sx(v)} x2={sx(v)} y1={height - PAD.bottom} y2={height - PAD.bottom + 4} className={styles.chartAxis} />
            <text x={sx(v)} y={height - PAD.bottom + 15} className={styles.chartTick} textAnchor="middle">
              {fmtTick(v)}
            </text>
          </g>
        ))}
        {(chart.bands ?? []).map((b, i) => {
          const x0 = sx(Math.max(b.x0, model.xmin)), x1 = sx(Math.min(b.x1, model.xmax));
          if (!(x1 > x0)) return null;
          return (
            <g key={`b${i}`}>
              <rect x={x0} y={PAD.top} width={x1 - x0} height={height - PAD.top - PAD.bottom} className={styles.chartBand} />
              {b.label && (
                <text x={(x0 + x1) / 2} y={PAD.top + 10} className={styles.chartBandLabel} textAnchor="middle">
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        {(chart.bands_y ?? []).map((b, i) => {
          const y0 = sy(Math.min(b.x1, model.ymax)), y1 = sy(Math.max(b.x0, model.ymin));
          if (!(y1 > y0)) return null;
          return (
            <g key={`by${i}`}>
              <rect x={PAD.left} y={y0} width={W - PAD.left - PAD.right} height={y1 - y0} className={styles.chartBand} />
              {b.label && (
                <text x={W - PAD.right - 4} y={y0 + 9} className={styles.chartBandLabel} textAnchor="end">
                  {b.label}
                </text>
              )}
            </g>
          );
        })}
        {zeroInRange && (
          <line x1={PAD.left} x2={W - PAD.right} y1={sy(0)} y2={sy(0)} className={styles.chartZero} />
        )}
        <line x1={PAD.left} x2={W - PAD.right} y1={height - PAD.bottom} y2={height - PAD.bottom} className={styles.chartAxis} />
        <line x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={height - PAD.bottom} className={styles.chartAxis} />
        {paths.map((d, i) =>
          d ? (
            <path key={chart.series[i].name} d={d} fill="none" stroke={SERIES_COLOURS[i % SERIES_COLOURS.length]} strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
          ) : null,
        )}
        {bars.map((rects, i) =>
          rects.map((r, j) => (
            <rect key={`${chart.series[i].name}-${j}`} x={r.x} y={r.y} width={r.w} height={r.h} fill={SERIES_COLOURS[i % SERIES_COLOURS.length]} opacity={0.85} rx={1} />
          )),
        )}
        {(chart.markers ?? []).map((m, i) => (
          <g key={i}>
            <circle cx={sx(m.x)} cy={sy(m.y)} r={3.5} className={styles.chartMarker} />
            {m.label && (
              <text x={sx(m.x) > W * 0.6 ? sx(m.x) - 6 : sx(m.x) + 6} y={Math.max(sy(m.y) - 6, PAD.top + 8)} className={styles.chartMarkerLabel} textAnchor={sx(m.x) > W * 0.6 ? "end" : "start"}>
                {m.label}
              </text>
            )}
          </g>
        ))}
        <text x={PAD.left + (W - PAD.left - PAD.right) / 2} y={height - 4} className={styles.chartLabel} textAnchor="middle">
          {chart.x_label}
        </text>
        <text transform={`translate(11 ${PAD.top + (height - PAD.top - PAD.bottom) / 2}) rotate(-90)`} className={styles.chartLabel} textAnchor="middle">
          {chart.y_label}
        </text>
      </svg>
      {chart.series.length > 1 && (
        <div className={styles.chartLegend}>
          {chart.series.map((s, i) => (
            <span key={s.name} className={styles.chartLegendItem}>
              <span className={styles.chartSwatch} style={{ background: SERIES_COLOURS[i % SERIES_COLOURS.length] }} />
              {s.name}
              {typeof s.n_source === "number" && s.n_source > s.x.length ? ` (${s.x.length} of ${s.n_source.toLocaleString()} points shown)` : ""}
            </span>
          ))}
        </div>
      )}
    </figure>
  );
}
