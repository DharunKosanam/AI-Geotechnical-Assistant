/**
 * Web-ingest surface gate + flow, pinned at the component boundary with a
 * mocked fetch — no browser, no network.
 *
 * The contract: the URL section renders NOTHING unless /api/kb/status carries
 * webIngest: true (flag off = the KB page is exactly today's); preview shows
 * the title + first lines BEFORE anything is indexed; each backend error code
 * renders its own specific heading, never a generic failure.
 */
import React from "react";
import { render, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { describe, test, expect, afterEach, vi } from "vitest";

import KbWebIngest from "../kb-web-ingest";

const jsonResponse = (data: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => data,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const mockFetch = (routes: Record<string, (init?: RequestInit) => any>) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      for (const [suffix, handler] of Object.entries(routes)) {
        if (String(url).endsWith(suffix)) return handler(init);
      }
      throw new Error(`unmocked fetch: ${url}`);
    }),
  );
};

describe("KbWebIngest gate", () => {
  test("renders nothing when kb status has no webIngest key (flag off)", async () => {
    mockFetch({ "/api/kb/status": () => jsonResponse({ enabled: true }) });
    const { container } = render(<KbWebIngest />);
    await waitFor(() => {
      expect((fetch as any).mock.calls.length).toBeGreaterThan(0);
    });
    expect(container.innerHTML).toBe("");
  });

  test("renders the URL field when webIngest is true", async () => {
    mockFetch({
      "/api/kb/status": () => jsonResponse({ enabled: true, webIngest: true }),
    });
    const { getByLabelText, getByText } = render(<KbWebIngest />);
    await waitFor(() => getByText("Add a web page by link"));
    expect(getByLabelText("Web page URL")).toBeTruthy();
  });
});

describe("KbWebIngest flow", () => {
  const enable = {
    "/api/kb/status": () => jsonResponse({ enabled: true, webIngest: true }),
  };

  test("preview shows title, resolved URL and first lines before ingest", async () => {
    mockFetch({
      ...enable,
      "/api/kb/web/preview": () =>
        jsonResponse({
          resolvedUrl: "https://www.uvic.ca/funding",
          title: "Travel funding - UVic",
          preview: "# Travel funding\n\nAmounts and deadlines…",
          charCount: 5287,
          textRatio: 0.1,
          ingestable: true,
          warnings: [],
          alreadyIngested: null,
        }),
    });
    const utils = render(<KbWebIngest />);
    await waitFor(() => utils.getByLabelText("Web page URL"));
    fireEvent.change(utils.getByLabelText("Web page URL"), {
      target: { value: "uvic.ca/funding" },
    });
    fireEvent.click(utils.getByText("Fetch preview"));
    await waitFor(() => utils.getByText(/Travel funding - UVic/));
    expect(utils.getByText(/https:\/\/www\.uvic\.ca\/funding/)).toBeTruthy();
    expect(utils.getByText(/Amounts and deadlines/)).toBeTruthy();
    // Nothing ingested yet; the confirm button exists and is disabled until
    // project + permission are given.
    const btn = utils.getByText("Add to Knowledge Base") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  test.each([
    ["not_allowlisted", "This site is not on the allowed list"],
    ["login_wall", "This page is behind a sign-in wall"],
    ["wrong_content_type", "Not a web page"],
    ["timeout", "The page timed out"],
    ["already_ingested", "Already in the knowledge base"],
  ])("error code %s renders its specific heading", async (code, heading) => {
    mockFetch({
      ...enable,
      "/api/kb/web/preview": () =>
        jsonResponse({ detail: { code, message: "backend detail text" } }, 422),
    });
    const utils = render(<KbWebIngest />);
    await waitFor(() => utils.getByLabelText("Web page URL"));
    fireEvent.change(utils.getByLabelText("Web page URL"), {
      target: { value: "https://x.example/" },
    });
    fireEvent.click(utils.getByText("Fetch preview"));
    await waitFor(() => utils.getByText(new RegExp(heading)));
    expect(utils.getByText(/backend detail text/)).toBeTruthy();
  });

  test("already-ingested preview offers Refresh and shows both dates after", async () => {
    mockFetch({
      ...enable,
      "/api/kb/web/preview": () =>
        jsonResponse({
          resolvedUrl: "https://www.uvic.ca/funding",
          title: "Travel funding - UVic",
          preview: "…",
          charCount: 5000,
          textRatio: 0.1,
          ingestable: true,
          warnings: [],
          alreadyIngested: { canonicalTitle: "Travel funding - UVic", fetchedAt: "2026-08-01T09:00:00", version: 1 },
        }),
      "/api/kb/web/ingest": () =>
        jsonResponse({
          batchId: "b2",
          canonicalUrl: "https://www.uvic.ca/funding",
          canonicalTitle: "Travel funding - UVic",
          fetchedAt: "2026-08-20T15:00:00",
          previousFetchedAt: "2026-08-01T09:00:00",
          contentChanged: false,
          version: 2,
          chunkCount: 4,
          superseded: 4,
        }),
    });
    const utils = render(<KbWebIngest />);
    await waitFor(() => utils.getByLabelText("Web page URL"));
    fireEvent.change(utils.getByLabelText("Web page URL"), {
      target: { value: "https://www.uvic.ca/funding" },
    });
    fireEvent.click(utils.getByText("Fetch preview"));
    await waitFor(() => utils.getByText("Refresh in Knowledge Base"));
    fireEvent.change(utils.getByPlaceholderText("e.g. uvic-funding"), {
      target: { value: "uvic-funding" },
    });
    fireEvent.click(utils.getByRole("checkbox"));
    fireEvent.click(utils.getByText("Refresh in Knowledge Base"));
    await waitFor(() => utils.getByText("Page refreshed"));
    expect(utils.getByText(/fetched 2026-08-20/)).toBeTruthy();
    expect(utils.getByText(/replaced copy from 2026-08-01/)).toBeTruthy();
    expect(utils.getByText(/content unchanged/)).toBeTruthy();
  });
});
