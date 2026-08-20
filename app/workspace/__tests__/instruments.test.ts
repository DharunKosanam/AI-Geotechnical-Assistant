import { describe, expect, test } from "vitest";

import { fmtBytes, statusLine, summaryTiles, type DatasetRecord } from "../instruments";

const base: DatasetRecord = {
  id: "d1", kind: "dataset", filename: "pass_001.tsv", parser_id: "odisi_tsv",
  dataset_kind: "strain_distributed", label: "DFOS", badge: "DFOS · 7795 gages",
  status: "parsed", progress: 100, metadata: {},
};

describe("summaryTiles", () => {
  test("DFOS tiles come from parser metadata only", () => {
    const tiles = summaryTiles({
      ...base,
      metadata: {
        n_gages: 7795, n_timesteps: 497, x_min_m: 0.08, x_max_m: 20.4424, gage_pitch_mm: 2.6,
        sample_rate_hz: 8.3333, duration_s: 59.52, tare_name: "0409", units: "microstrain",
      },
    });
    const byLabel = Object.fromEntries(tiles.map((t) => [t.label, t.value]));
    expect(byLabel["Gages"]).toBe("7,795");
    expect(byLabel["Timesteps"]).toBe("497");
    expect(byLabel["Fibre span (m)"]).toBe("0.08 → 20.44");
    expect(byLabel["Rate (Hz)"]).toBe("8.333");
    expect(byLabel["Tare"]).toBe("0409");
  });

  test("pressure tiles include one max tile per detected channel (count is data)", () => {
    const tiles = summaryTiles({
      ...base,
      dataset_kind: "pressure_timeseries", label: "Pressure", badge: "Pressure · 2 channels",
      metadata: {
        n_channels: 2, n_samples: 864000, sample_rate_hz: 10, duration_s: 86399.9,
        first_timestamp: "2024-05-10T00:00:00.000", last_timestamp: "2024-05-10T23:59:59.900",
        channel_names: ["A_kPa", "B_kPa"], column_max: [29.96, 27.87],
      },
    });
    const labels = tiles.map((t) => t.label);
    expect(labels).toContain("Max A_kPa");
    expect(labels).toContain("Max B_kPa");
    expect(labels.filter((l) => l.startsWith("Max "))).toHaveLength(2);
    expect(tiles.find((t) => t.label === "Samples")?.value).toBe("864,000");
    expect(tiles.find((t) => t.label === "First sample")?.value).toBe("2024-05-10 00:00:00.000");
  });

  test("missing numbers render as an em dash, never NaN", () => {
    const tiles = summaryTiles({ ...base, metadata: {} });
    expect(tiles.every((t) => !t.value.includes("NaN"))).toBe(true);
    expect(tiles.find((t) => t.label === "Gages")?.value).toBe("—");
  });
});

describe("statusLine / fmtBytes", () => {
  test("status lines", () => {
    expect(statusLine({ ...base, status: "queued" })).toBe("Queued for parsing…");
    expect(statusLine({ ...base, status: "parsing", progress: 42.6 })).toBe("Parsing… 43%");
    expect(statusLine({ ...base, status: "failed", error: "boom" })).toBe("boom");
    expect(statusLine(base)).toBe("DFOS · 7795 gages");
  });
  test("bytes", () => {
    expect(fmtBytes(27271898)).toBe("26.0 MB");
    expect(fmtBytes(2048)).toBe("2 KB");
    expect(fmtBytes(undefined)).toBe("");
  });
});
