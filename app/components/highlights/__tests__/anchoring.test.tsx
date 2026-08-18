/**
 * Highlight anchoring, exercised through the REAL react-markdown pipeline
 * (remark-gfm + remark-math + rehype-katex + rehypeHighlights) rendered into
 * jsdom -- no mocks of the renderer. Each case renders a source, builds a
 * browser Selection over the RENDERED text, resolves it to source offsets, and
 * checks the source slice, the stored selectedText, and the render round-trip
 * (a highlight at those offsets draws a <mark> around exactly that text).
 *
 * "REFUSED" cases assert null: the student sees nothing happen. Never a
 * highlight in the wrong place.
 */
import React from "react";
import { render, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

import { rehypeHighlights, type HighlightForRender, type LayoutReport } from "../rehype-highlights";
import { collectTextNodes, resolveDomSelection, layoutMatchesDom } from "../dom-selection";
import { alignValueToSource, renderedTextInRange } from "../anchoring";

afterEach(cleanup);

function renderMd(source: string, highlights: HighlightForRender[] = []) {
  let report: LayoutReport | null = null;
  const { container } = render(
    <Markdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex, [rehypeHighlights, { source, highlights, onLayout: (r: LayoutReport) => (report = r) }]]}
    >
      {source}
    </Markdown>,
  );
  if (!report) throw new Error("plugin did not report a layout");
  return { container: container as HTMLElement, report: report as LayoutReport };
}

/** Rendered (KaTeX-free) text of the container, as the walker sees it. */
const walkedText = (container: HTMLElement) =>
  collectTextNodes(container).map((n) => n.data).join("");

/** Map an index into walkedText -> (text node, offset). */
function locate(container: HTMLElement, index: number, forEnd = false): [Text, number] {
  let acc = 0;
  const nodes = collectTextNodes(container);
  for (const n of nodes) {
    const len = n.data.length;
    if (index < acc + len || (forEnd && index === acc + len)) return [n, index - acc];
    acc += len;
  }
  throw new Error(`index ${index} out of range`);
}

/** Select `needle` (must occur once in the walked text) and resolve it. */
function selectAndResolve(container: HTMLElement, report: LayoutReport, needle: string, reasons: string[] = []) {
  const text = walkedText(container);
  const at = text.indexOf(needle);
  if (at < 0 || text.indexOf(needle, at + 1) >= 0) throw new Error(`needle not unique: ${JSON.stringify(needle)} in ${JSON.stringify(text)}`);
  const [sn, so] = locate(container, at);
  const [en, eo] = locate(container, at + needle.length, true);
  return resolveWith(container, report, sn, so, en, eo, reasons);
}

function resolveWith(
  container: HTMLElement,
  report: LayoutReport,
  sn: Node, so: number, en: Node, eo: number,
  reasons: string[] = [],
) {
  const range = document.createRange();
  range.setStart(sn, so);
  range.setEnd(en, eo);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  return resolveDomSelection(container, sel, report.fragments, (r) => reasons.push(r));
}

/** Round-trip: a highlight at the resolved range must render as one or more
 *  <mark> elements whose concatenated text is exactly the selected text. */
function expectRoundTrip(source: string, range: { start: number; end: number; selectedText: string }) {
  const h: HighlightForRender = {
    id: "h1", startOffset: range.start, endOffset: range.end, selectedText: range.selectedText,
    colour: "yellow", note: "", createdAt: "2026-08-17T00:00:00",
  };
  const { container, report } = renderMd(source, [h]);
  const marks = Array.from(container.querySelectorAll("mark[data-hl-id='h1']"));
  expect(report.invalid).toEqual([]);
  expect(marks.length).toBeGreaterThan(0);
  expect(marks.map((m) => m.textContent).join("")).toBe(range.selectedText);
  // The DOM after splitting still matches the reported layout node-for-node.
  expect(layoutMatchesDom(collectTextNodes(container), report.fragments)).toBe(true);
  return container;
}

describe("aligner: escaped characters", () => {
  test("\\* and \\_ map to the escaped source and round-trip", () => {
    const source = "Use \\*not emphasis\\* and snake\\_case names";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("Use *not emphasis* and snake_case names");
    const r = selectAndResolve(container, report, "*not emphasis* and snake_case")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("\\*not emphasis\\* and snake\\_case");
    expect(r.range.selectedText).toBe("*not emphasis* and snake_case");
    expectRoundTrip(source, r.range);
  });
});

describe("aligner: HTML entities", () => {
  test("&amp; and &lt; decode to one rendered char over the whole reference", () => {
    const source = "Tom &amp; Jerry &lt; 3 friends";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("Tom & Jerry < 3 friends");
    const r = selectAndResolve(container, report, "& Jerry <")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("&amp; Jerry &lt;");
    expect(r.range.selectedText).toBe("& Jerry <");
    expectRoundTrip(source, r.range);
  });

  test("an entity outside the aligner's table makes its node unmappable: endpoint REFUSED", () => {
    // micromark decodes &eacute; -> "é"; the aligner only knows a short list of
    // references, so it gives up on this node rather than guess a position.
    const source = "caf&eacute; au lait\n\ngamma";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("café au lait\ngamma");
    expect(report.fragments[0].starts).toBeNull();
    const reasons: string[] = [];
    expect(selectAndResolve(container, report, "au lait", reasons)).toBeNull();
    expect(reasons).toEqual(["unmappable"]);
    // The next paragraph is unaffected.
    const r = selectAndResolve(container, report, "gamma")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("gamma");
  });

  test("an unknown reference like &zzzz; is left literal by markdown and aligns 1:1", () => {
    const source = "alpha &zzzz; beta";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("alpha &zzzz; beta");
    const r = selectAndResolve(container, report, "&zzzz; beta")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("&zzzz; beta");
    expectRoundTrip(source, r.range);
  });
});

describe("aligner: inline code backticks at a selection boundary", () => {
  const source = "use `qc` values here";

  test("selection exactly the code content starts after the backtick", () => {
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "qc")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("qc");
    expectRoundTrip(source, r.range);
  });

  test("selection ending inside the code span", () => {
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "use qc")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("use `qc");
    expect(r.range.selectedText).toBe("use qc");
    expectRoundTrip(source, r.range);
  });

  test("selection starting inside the code span", () => {
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "qc values")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("qc` values");
    expect(r.range.selectedText).toBe("qc values");
    expectRoundTrip(source, r.range);
  });

  test("double-backtick code span with padding", () => {
    const src = "run `` a`b `` now";
    const { container, report } = renderMd(src);
    expect(walkedText(container)).toBe("run a`b now");
    const r = selectAndResolve(container, report, "a`b now")!;
    expect(r.range.selectedText).toBe("a`b now");
    expectRoundTrip(src, r.range);
  });
});

describe("aligner: soft line break inside one paragraph", () => {
  test("indented continuation line: rendered \\n maps to newline + indentation", () => {
    const source = "first line of prose\n   second line of prose";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("first line of prose\nsecond line of prose");
    const r = selectAndResolve(container, report, "prose\nsecond")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("prose\n   second");
    expect(r.range.selectedText).toBe("prose\nsecond");
    expectRoundTrip(source, r.range);
  });

  test("a trailing space before the soft break is absorbed", () => {
    const source = "alpha beta \ngamma delta";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("alpha beta\ngamma delta");
    const r = selectAndResolve(container, report, "beta\ngamma")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("beta \ngamma");
    expect(r.range.selectedText).toBe("beta\ngamma");
    expectRoundTrip(source, r.range);
  });

  test("a hard break (two+ trailing spaces) renders <br> + a layout newline that carries no text", () => {
    const source = "alpha beta   \ngamma delta";
    const { container, report } = renderMd(source);
    expect(container.querySelector("br")).toBeTruthy();
    expect(walkedText(container)).toBe("alpha beta\ngamma delta");
    const r = selectAndResolve(container, report, "beta\ngamma")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("beta   \ngamma");
    // the "\n" after <br> is unpositioned layout text: skipped, not stored
    expect(r.range.selectedText).toBe("betagamma");
    expectRoundTrip(source, r.range);
  });

  test("entities and escapes inside code are literal", () => {
    const source = "call `a &amp; b` and `x \\* y`";
    const { container, report } = renderMd(source);
    expect(walkedText(container)).toBe("call a &amp; b and x \\* y");
    const r = selectAndResolve(container, report, "a &amp; b")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("a &amp; b");
    expectRoundTrip(source, r.range);
    const r2 = selectAndResolve(container, report, "x \\* y")!;
    expect(source.slice(r2.range.start, r2.range.end)).toBe("x \\* y");
    expectRoundTrip(source, r2.range);
  });

  test("a bare & that is not a reference aligns 1:1", () => {
    const source = "R & D and A&B";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "& D and A&B")!;
    expect(source.slice(r.range.start, r.range.end)).toBe("& D and A&B");
    expectRoundTrip(source, r.range);
  });
});

describe("REFUSED: endpoint inside math or at an image", () => {
  test("endpoint inside KaTeX output is refused", () => {
    const source = "friction angle $\\phi$ matters";
    const { container, report } = renderMd(source);
    const katexText = document.createTreeWalker(container.querySelector(".katex")!, NodeFilter.SHOW_TEXT).nextNode() as Text;
    expect(katexText).toBeTruthy();
    const [sn, so] = locate(container, 0);
    const reasons: string[] = [];
    expect(resolveWith(container, report, sn, so, katexText, 1, reasons)).toBeNull();
    expect(reasons).toEqual(["boundary"]);
    // ...but a selection that merely SPANS the math is fine; the math contributes no text.
    const r = selectAndResolve(container, report, "angle  matters")!; // walked text has "angle " + " matters"
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("angle $\\phi$ matters");
    expect(r.range.selectedText).toBe("angle  matters");
    expectRoundTrip(source, r.range);
  });

  test("selection boundary at an image is refused (both sides)", () => {
    const source = "see ![alt text](http://x/y.png) here";
    const { container, report } = renderMd(source);
    const p = container.querySelector("p")!;
    const img = container.querySelector("img")!;
    const imgIndex = Array.from(p.childNodes).indexOf(img);
    const [sn, so] = locate(container, 0);
    // end boundary just after the img element
    let reasons: string[] = [];
    expect(resolveWith(container, report, sn, so, p, imgIndex + 1, reasons)).toBeNull();
    expect(reasons).toEqual(["boundary"]);
    // start boundary at the img element
    reasons = [];
    const [en, eo] = locate(container, walkedText(container).length, true);
    expect(resolveWith(container, report, p, imgIndex, en, eo, reasons)).toBeNull();
    expect(reasons).toEqual(["boundary"]);
    // spanning the image between two text nodes is fine
    const r = selectAndResolve(container, report, "see  here")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("see ![alt text](http://x/y.png) here");
    expectRoundTrip(source, r.range);
  });
});

describe("aligner: selection spanning two markdown nodes", () => {
  test("across **bold**: the source range includes the delimiters, selectedText does not", () => {
    const source = "The **bearing capacity** of soil";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "The bearing capacity of")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("The **bearing capacity** of");
    expect(r.range.selectedText).toBe("The bearing capacity of");
    const c = expectRoundTrip(source, r.range);
    // Two marks: one in the paragraph text, one inside <strong>, one after.
    expect(c.querySelectorAll("mark").length).toBe(3);
    expect(c.querySelector("strong mark")!.textContent).toBe("bearing capacity");
  });

  test("across a list item boundary and a table cell", () => {
    const source = "- first item\n- second item\n\n| a | b |\n|---|---|\n| c1 | d1 |";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "item\nsecond")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("item\n- second");
    expectRoundTrip(source, r.range);
    const r2 = selectAndResolve(container, report, "c1")!;
    expect(source.slice(r2.range.start, r2.range.end)).toBe("c1");
    expectRoundTrip(source, r2.range);
  });
});

describe("aligner: other markdown constructs", () => {
  test("blockquote continuation marker is skipped", () => {
    const source = "> quote line one\n> quote line two";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "one\nquote line two")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("one\n> quote line two");
    expectRoundTrip(source, r.range);
  });

  test("fenced code block content maps inside the fences", () => {
    const source = "Run:\n\n```python\nx = 1\ny = qc * 2\n```\n\nDone.";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "y = qc")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("y = qc");
    expectRoundTrip(source, r.range);
    // whole block incl. the renderer-appended final newline
    const all = selectAndResolve(container, report, "x = 1\ny = qc * 2\n")!;
    expect(source.slice(all.range.start, all.range.end)).toBe("x = 1\ny = qc * 2\n");
    expectRoundTrip(source, all.range);
  });

  test("fenced code block inside a list item (indented fences and lines)", () => {
    const source = "- step\n\n  ```\n  a = 1\n  b = 2\n  ```\n- next";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "a = 1\nb")!;
    expect(r).not.toBeNull();
    expect(source.slice(r.range.start, r.range.end)).toBe("a = 1\n  b");
    expectRoundTrip(source, r.range);
  });

  test("triple-click style element boundaries resolve to the paragraph's text", () => {
    const source = "First paragraph here.\n\nSecond paragraph.";
    const { container, report } = renderMd(source);
    const [p1, p2] = Array.from(container.querySelectorAll("p"));
    // Firefox style: (p1, 0) .. (p1, childCount)
    const r1 = resolveWith(container, report, p1, 0, p1, p1.childNodes.length)!;
    expect(r1.range.selectedText).toBe("First paragraph here.");
    // Chrome style: (text, 0) .. (p2's text, 0) -> ends at the end of p1's text
    const r2 = resolveWith(container, report, p1.firstChild!, 0, p2.firstChild!, 0)!;
    expect(r2.range.selectedText).toBe("First paragraph here.");
    expect(source.slice(r2.range.start, r2.range.end)).toBe("First paragraph here.");
    // Layout whitespace between blocks: an endpoint in it snaps inward.
    const layoutWs = collectTextNodes(container).find((n) => n.data === "\n")!;
    expect(layoutWs).toBeTruthy();
    const r3 = resolveWith(container, report, p1.firstChild!, 6, layoutWs, 0)!;
    expect(r3.range.selectedText).toBe("paragraph here.");
  });

  test("selection with no rendered characters is refused", () => {
    const source = "First paragraph here.\n\nSecond paragraph.";
    const { container, report } = renderMd(source);
    const layoutWs = collectTextNodes(container).find((n) => n.data === "\n")!;
    const reasons: string[] = [];
    expect(resolveWith(container, report, layoutWs, 0, layoutWs, 1, reasons)).toBeNull();
    expect(reasons).toEqual(["unmappable"]);
  });
});

describe("render integrity", () => {
  test("a stored highlight whose selectedText no longer matches is NOT drawn and is reported", () => {
    const source = "The bearing capacity of soil";
    const bad: HighlightForRender = {
      id: "stale", startOffset: 4, endOffset: 20, selectedText: "bearing strength",
      colour: "green", note: "", createdAt: "2026-08-17T00:00:00",
    };
    const { container, report } = renderMd(source, [bad]);
    expect(container.querySelectorAll("mark").length).toBe(0);
    expect(report.invalid).toEqual(["stale"]);
    // and the DOM is exactly the unhighlighted rendering
    expect(container.innerHTML).toBe(renderMd(source).container.innerHTML);
  });

  test("overlapping highlights: the most recently created one wins on the overlap", () => {
    const source = "alpha beta gamma delta";
    const older: HighlightForRender = { id: "old", startOffset: 0, endOffset: 16, selectedText: "alpha beta gamma", colour: "yellow", note: "", createdAt: "2026-08-17T00:00:00" };
    const newer: HighlightForRender = { id: "new", startOffset: 6, endOffset: 22, selectedText: "beta gamma delta", colour: "blue", note: "why", createdAt: "2026-08-17T00:00:01" };
    const { container, report } = renderMd(source, [newer, older]);
    expect(report.invalid).toEqual([]);
    const marks = Array.from(container.querySelectorAll("mark")).map((m) => [m.getAttribute("data-hl-id"), m.textContent, m.className, m.getAttribute("title")]);
    expect(marks).toEqual([
      ["old", "alpha ", "hl hl-yellow", null],
      ["new", "beta gamma delta", "hl hl-blue", "why"],
    ]);
  });

  test("DOM that no longer matches the layout is refused", () => {
    const source = "plain text here";
    const { container, report } = renderMd(source);
    container.querySelector("p")!.appendChild(document.createTextNode(" injected"));
    const reasons: string[] = [];
    expect(selectAndResolve(container, report, "plain", reasons)).toBeNull();
    expect(reasons).toEqual(["layout-mismatch"]);
  });

  test("offsets are UTF-16 units: an astral char before the selection", () => {
    const source = "\u{1F600} friction angle";
    const { container, report } = renderMd(source);
    const r = selectAndResolve(container, report, "friction")!;
    // JS slice (UTF-16) agrees, which is what the server's utf16_slice checks.
    expect(source.slice(r.range.start, r.range.end)).toBe("friction");
    expect(r.range.start).toBe(3);
    expectRoundTrip(source, r.range);
  });
});

describe("alignValueToSource unit cases", () => {
  test("identity", () => {
    expect(alignValueToSource("abc", "abc", 10)).toEqual({ starts: [10, 11, 12], ends: [11, 12, 13] });
  });
  test("value longer than what the source can supply -> null", () => {
    expect(alignValueToSource("abcd", "abc", 0)).toBeNull();
  });
  test("mismatched character -> null (never guesses)", () => {
    expect(alignValueToSource("abd", "abc", 0)).toBeNull();
  });
  test("numeric entities, decimal and hex", () => {
    expect(alignValueToSource("A", "&#65;", 0)).toEqual({ starts: [0], ends: [5] });
    expect(alignValueToSource("A", "&#x41;", 0)).toEqual({ starts: [0], ends: [6] });
  });
  test("renderedTextInRange ignores unmappable fragments", () => {
    const frags = [
      { value: "ab", starts: [0, 1], ends: [1, 2] },
      { value: "\n", starts: null, ends: null },
      { value: "cd", starts: [5, 6], ends: [6, 7] },
    ];
    expect(renderedTextInRange(frags, 1, 6)).toBe("bc");
  });
});
