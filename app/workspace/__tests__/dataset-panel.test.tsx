/**
 * INSTRUMENT_PARSERS_ENABLED at the GeoPilot page boundary, with fetch mocked
 * per URL (no browser, no network).
 *
 * Flag off (status = {enabled:true} only): the page renders exactly as before
 * -- no "Datasets" group, no dataset requests, the .cpt-only picker, uploads
 * go to /api/workspace/documents. Flag on: the two labelled groups appear,
 * uploads go through the streaming /api/workspace/upload proxy, a sniffed
 * dataset lands in the Datasets group with an on-row progress bar, its ONE
 * thread message updates in place from progress to the summary card (which
 * says no calculation has run), a failed row shows the error + retry.
 */
import React from "react";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/workspace",
}));

import WorkspacePage from "../page";
import { DatasetRow } from "../components/dataset-rows";
import { DatasetMessage } from "../components/dataset-cards";
import type { DatasetRecord } from "../instruments";

const json = (data: unknown, status = 200) => ({
  ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data),
  headers: new Headers({ "content-type": "application/json" }),
});

let calls: { url: string; method: string }[];
let chatReply: Record<string, unknown> = { type: "info", text: "I can run: CPT interpretation - say 'run cpt'." };
let statusPayload: Record<string, unknown>;
let jobState: { state: string; progress: number; error?: string | null };
let datasetDetail: DatasetRecord;

const parsedDetail: DatasetRecord = {
  id: "ds1", kind: "dataset", filename: "pass_001.tsv", parser_id: "odisi_tsv",
  dataset_kind: "strain_distributed", label: "DFOS", badge: "DFOS · 7795 gages",
  status: "parsed", progress: 100, error: null, job_id: "job1",
  metadata: { n_gages: 7795, n_timesteps: 497, x_min_m: 0.08, x_max_m: 20.4424, gage_pitch_mm: 2.6, sample_rate_hz: 8.3333, duration_s: 59.52, tare_name: "0409", units: "microstrain" },
  warnings: [], segments: [],
};

function installFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method });
      if (url.endsWith("/auth/me")) return json({ id: "u1", email: "t@example.com", full_name: "T", role: "user" });
      if (url === "/api/workspace/status") return json(statusPayload);
      if (url === "/api/kb/status") return json({ enabled: false });
      if (url === "/api/workspace/history/runs") return json({ runs: [] });
      if (url === "/api/workspace/history/threads") return json({ threads: [] });
      if (url === "/api/workspace/datasets" && method === "GET") return json({ datasets: [] });
      if (url === "/api/workspace/upload" && method === "POST")
        return json({ id: "ds1", kind: "dataset", filename: "pass_001.tsv", extension: ".tsv", status: "queued", progress: 0, dataset_id: "ds1", job_id: "job1", parser_id: "odisi_tsv", dataset_kind: "strain_distributed", label: "DFOS", badge: "DFOS", size_bytes: 27271898 }, 201);
      if (url === "/api/workspace/documents" && method === "POST")
        return json({ id: "doc-1", filename: "s.cpt", extension: ".cpt", status: "ready" }, 201);
      if (url === "/api/workspace/datasets/jobs/job1") return json({ id: "job1", dataset_id: "ds1", ...jobState });
      if (url === "/api/workspace/datasets/ds1" && method === "GET") return json(datasetDetail);
      if (url === "/api/workspace/chat" && method === "POST") return json(chatReply);
      return json({ detail: "unexpected " + method + " " + url }, 404);
    }),
  );
}

beforeEach(() => {
  statusPayload = { enabled: true };
  jobState = { state: "parsing", progress: 40 };
  datasetDetail = parsedDetail;
  installFetch();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderPage() {
  const utils = render(<WorkspacePage />);
  await waitFor(() => expect(utils.getByText("GeoPilot workspace")).toBeTruthy());
  return utils;
}

function pickFile(container: HTMLElement, name: string) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["x"], name, { type: "application/octet-stream" });
  fireEvent.change(input, { target: { files: [file] } });
  return input;
}

describe("flag off (status has no instrument_parsers)", () => {
  test("renders the pre-feature panel: no groups, .cpt picker, no dataset requests", async () => {
    const { container, queryByText } = await renderPage();
    expect(queryByText("Datasets")).toBeNull();
    expect(container.querySelector('input[type="file"]')?.getAttribute("accept")).toBe(".cpt,.CPT");
    expect(queryByText(/instrument files/i)).toBeNull();
    expect(calls.some((c) => c.url.startsWith("/api/workspace/datasets"))).toBe(false);
  });

  test("uploads still go to /api/workspace/documents via the rewrite", async () => {
    const { container, findByText } = await renderPage();
    pickFile(container, "s.cpt");
    await findByText("s.cpt");
    const uploads = calls.filter((c) => c.method === "POST");
    expect(uploads.map((c) => c.url)).toEqual(["/api/workspace/documents"]);
  });
});

describe("flag on (status advertises instrument_parsers)", () => {
  beforeEach(() => {
    statusPayload = { enabled: true, instrument_parsers: true, instrument_extensions: [".tsv", ".txt", ".dat", ".csv"] };
  });

  test("shows Documents + Datasets groups, widens the picker, lists datasets", async () => {
    const { container, getByText, getAllByText } = await renderPage();
    await waitFor(() => expect(getByText("Datasets")).toBeTruthy());
    expect(getAllByText("Documents").length).toBe(2); // tab + group label
    expect(container.querySelector('input[type="file"]')?.getAttribute("accept")).toBe(".cpt,.CPT,.tsv,.txt,.dat,.csv");
    expect(calls.some((c) => c.url === "/api/workspace/datasets" && c.method === "GET")).toBe(true);
  });

  test("a sniffed upload becomes a dataset row with progress, then ONE in-place message becomes the summary card", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { container, getByText, findByText, queryAllByText } = await renderPage();
      await waitFor(() => expect(getByText("Datasets")).toBeTruthy());
      pickFile(container, "pass_001.tsv");
      // Upload went through the streaming proxy, not the rewrite.
      await waitFor(() => expect(calls.some((c) => c.url === "/api/workspace/upload" && c.method === "POST")).toBe(true));
      expect(calls.some((c) => c.url === "/api/workspace/documents" && c.method === "POST")).toBe(false);
      // Row appears in the Datasets group with a progress bar (after first poll: 40%).
      await waitFor(() => expect(container.querySelector('[role="progressbar"]')).toBeTruthy());
      await waitFor(() => expect(container.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("40"));
      // Exactly one thread message for the dataset (progress line).
      expect(queryAllByText(/Parsing/).length).toBeGreaterThan(0);
      const progressMsgs = container.querySelectorAll('[role="status"]');
      expect(progressMsgs.length).toBe(1);
      // Job finishes -> detail fetched -> same message becomes the summary card.
      jobState = { state: "parsed", progress: 100 };
      await act(async () => {
        vi.advanceTimersByTime(1100);
      });
      await findByText(/No calculation has run on this dataset/);
      expect(container.querySelectorAll('[role="status"]').length).toBe(0); // progress line gone (replaced, not appended)
      expect(getByText("Gages").nextSibling?.textContent).toBe("7,795");
      expect(getByText("run dfos pass strain")).toBeTruthy();
      // Row now shows the detected badge, no progress bar.
      expect(container.querySelector('[role="progressbar"]')).toBeNull();
      expect(queryAllByText("DFOS · 7795 gages").length).toBeGreaterThan(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("components", () => {
  test("failed row shows the error and a retry control; retry calls back", () => {
    const onRetry = vi.fn();
    const ds: DatasetRecord = { ...parsedDetail, status: "failed", progress: 0, error: "Could not parse this file: no data rows." };
    const { getByText, getByLabelText } = render(<DatasetRow ds={ds} onRemove={() => {}} onRetry={onRetry} />);
    expect(getByText("Could not parse this file: no data rows.")).toBeTruthy();
    fireEvent.click(getByLabelText("Retry parsing pass_001.tsv"));
    expect(onRetry).toHaveBeenCalledWith(ds);
  });

  test("dataset row expands to show segments as children", () => {
    const ds: DatasetRecord = {
      ...parsedDetail, dataset_kind: "pressure_timeseries", label: "Pressure", badge: "Pressure · 4 channels",
      segments: [
        { index: 1, label: "Event 1 · 07:12:03", peak_sum_kpa: 82.4, duration_s: 1.3, direction: "TP4144 → TP4149" },
        { index: 2, label: "Event 2 · 07:15:40", peak_sum_kpa: 61.0, duration_s: 0.9, direction: "TP4149 → TP4144" },
      ],
    };
    const { getByLabelText, queryByText, getByText } = render(<DatasetRow ds={ds} onRemove={() => {}} onRetry={() => {}} />);
    expect(getByText("Pressure · 4 channels · 2 events")).toBeTruthy();
    expect(queryByText("Event 1 · 07:12:03")).toBeNull();
    fireEvent.click(getByLabelText("Expand 2 segments"));
    expect(getByText("Event 1 · 07:12:03")).toBeTruthy();
    expect(getByText("Event 2 · 07:15:40")).toBeTruthy();
  });

  test("summary card renders parser warnings and the no-calculation line", () => {
    const { getByText } = render(<DatasetMessage ds={{ ...parsedDetail, warnings: ["Tare length 7794 differs from x-axis length 7795"] }} />);
    expect(getByText(/Tare length 7794/)).toBeTruthy();
    expect(getByText(/No calculation has run/)).toBeTruthy();
    expect(getByText("Parser warnings")).toBeTruthy();
  });
});

describe("dataset result card", () => {
  beforeEach(() => {
    statusPayload = { enabled: true, instrument_parsers: true, instrument_extensions: [".tsv", ".dat"] };
    chatReply = {
      type: "result", calculator_id: "traffic_load_monitoring", calculator_name: "Traffic load monitoring",
      source_file: "2024-05-10.dat", reference: "Provisional threshold method ...", params: {},
      summary_text: "Detected 312 events.", layers: [], metadata: {},
      summary: { "Source file": "2024-05-10.dat", "Events detected": 312, "Validation status": "PROVISIONAL", "Method / Standard reference": "x" },
      charts: [{ id: "hourly", title: "Events per hour", x_label: "Hour", y_label: "Events", series: [{ name: "events", x: [0, 1, 2], y: [3, 5, 2] }] }],
      notices: [{ level: "provisional", text: "Threshold method PROVISIONAL - pending engineering validation." }],
      dataset_id: "ds1", dataset_kind: "pressure_timeseries",
      segments: [{ index: 1, label: "Event 1", peak_sum_kpa: 80 }],
      interpretation: { narrative: "Draft text for review.", is_ai_draft: true },
      result_id: "r1", run_id: "run1", exportable: true, thread_id: "t1",
    };
  });

  test("renders notice + deterministic block + chart + export, AI draft collapsed until asked", async () => {
    const { container, getByText, queryByText, getByPlaceholderText } = await renderPage();
    await waitFor(() => expect(getByText("Datasets")).toBeTruthy());
    const ta = getByPlaceholderText(/Message GeoPilot/);
    fireEvent.change(ta, { target: { value: "run traffic load monitoring" } });
    fireEvent.submit(ta.closest("form")!);
    await waitFor(() => expect(getByText("Detected 312 events.")).toBeTruthy());
    // Provisional notice visible in the deterministic block (not a tooltip).
    expect(getByText(/PROVISIONAL - pending engineering validation/)).toBeTruthy();
    expect(getByText("Provisional")).toBeTruthy();
    // Deterministic rows from summary; reference NOT duplicated in the grid.
    expect(getByText("Events detected").nextSibling?.textContent).toBe("312");
    expect(container.querySelectorAll("figure svg").length).toBe(1);
    expect(getByText("Export to Excel")).toBeTruthy();
    // AI draft collapsed behind the affordance.
    expect(queryByText("Draft text for review.")).toBeNull();
    fireEvent.click(getByText(/Show AI draft interpretation/));
    expect(getByText("Draft text for review.")).toBeTruthy();
    expect(getByText("AI draft — for engineer review")).toBeTruthy();
    // No CPT layers table for a dataset result.
    expect(queryByText("Soil behaviour type")).toBeNull();
  });
});
