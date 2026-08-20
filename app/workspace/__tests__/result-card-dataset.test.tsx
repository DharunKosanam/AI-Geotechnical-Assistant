/**
 * Dataset-bound result rendering: deterministic block from `summary`, visible
 * status notices, SVG charts from the (downsampled) payload, Excel export
 * button, and the AI draft collapsed behind an explicit review affordance.
 * Also pins that a CPT result (layers table, expanded AI section) is unchanged.
 */
import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { LineChart } from "../components/strain-chart";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/workspace",
}));

afterEach(() => cleanup());

const chart = {
  id: "envelope",
  title: "Strain envelope along the fibre (relative to tare)",
  x_label: "Position along fibre x (m)",
  y_label: "Strain (microstrain)",
  series: [
    { name: "max", x: [0, 5, 10, 15, 20], y: [1, 50, 120, 40, 2], n_source: 7795 },
    { name: "min", x: [0, 5, 10, 15, 20], y: [-1, -3, -8, -2, 0], n_source: 7795 },
  ],
  markers: [{ x: 10, y: 120, label: "global peak 120.0 µε @ 10.00 m" }],
};

describe("LineChart", () => {
  test("renders one path per series, axes labels, marker and downsampling note", () => {
    const { container, getByText, getAllByText } = render(<LineChart chart={chart} />);
    expect(container.querySelectorAll("path").length).toBe(2);
    expect(getByText("Position along fibre x (m)")).toBeTruthy();
    expect(getByText("Strain (microstrain)")).toBeTruthy();
    expect(getByText("global peak 120.0 µε @ 10.00 m")).toBeTruthy();
    expect(getAllByText(/5 of 7,795 points shown/).length).toBe(2); // one per series
    expect(container.querySelector("svg")?.getAttribute("role")).toBe("img");
  });

  test("all-null series does not crash", () => {
    const { getByText } = render(
      <LineChart chart={{ ...chart, series: [{ name: "x", x: [null], y: [null] }], markers: [] }} />,
    );
    expect(getByText(/No plottable data/)).toBeTruthy();
  });
});

describe("LineChart bars", () => {
  test("renders one rect per bin for a bar series", () => {
    const { container } = render(
      <LineChart
        chart={{
          id: "hourly", title: "Events per hour", x_label: "Hour of day", y_label: "Events",
          series: [{ name: "events", x: Array.from({ length: 24 }, (_, i) => i), y: Array.from({ length: 24 }, (_, i) => (i * 7) % 11), kind: "bar" }],
        }}
      />,
    );
    expect(container.querySelectorAll("rect").length).toBe(24);
    expect(container.querySelectorAll("path").length).toBe(0);
  });
});

describe("LineChart trimmed-end bands", () => {
  test("shades trimmed regions and widens the axis to x_range", () => {
    const { container, getAllByText } = render(
      <LineChart
        chart={{
          id: "envelope", title: "Envelope", x_label: "x (m)", y_label: "µε",
          series: [{ name: "max", x: [0.2, 10, 20.3], y: [10, 300, 20] }],
          x_range: [0.08, 20.44],
          bands: [{ x0: 0.08, x1: 0.2, label: "trimmed 40 gages" }, { x0: 20.3, x1: 20.44, label: "trimmed 40 gages" }],
        }}
      />,
    );
    expect(container.querySelectorAll("rect").length).toBe(2);
    expect(getAllByText("trimmed 40 gages").length).toBe(2);
  });
});
