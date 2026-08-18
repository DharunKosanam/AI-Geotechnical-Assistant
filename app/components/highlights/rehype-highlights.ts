/**
 * rehype plugin: (1) describes the message's rendered text nodes as Fragments
 * with per-character source ranges, and (2) wraps the parts of them covered by
 * valid stored highlights in <mark data-hl-id> elements -- at the hast level,
 * so React renders the marks natively and no DOM is mutated behind its back.
 *
 * Runs AFTER rehype-katex so KaTeX output is present and can be skipped
 * (its text is duplicated MathML + HTML and has no source positions).
 *
 * A stored highlight is drawn only if the rendered text at its source range
 * equals its stored selectedText exactly; otherwise it is reported as invalid
 * and nothing is drawn -- never a mark at the wrong place.
 */
import type { Element, ElementContent, Parent, Root, Text } from "hast";

import {
  alignValueToSource,
  codeBlockSlice,
  renderedTextInRange,
  type Fragment,
} from "./anchoring";

export type HighlightForRender = {
  id: string;
  startOffset: number;
  endOffset: number;
  selectedText: string;
  colour: string;
  note: string;
  createdAt: string;
};

export type LayoutReport = {
  /** Rendered text nodes in document order, AFTER splitting around marks. */
  fragments: Fragment[];
  /** Highlight ids whose stored text no longer matches -> not drawn. */
  invalid: string[];
};

export type RehypeHighlightsOptions = {
  /** The exact string given to react-markdown (offsets are into this). */
  source: string;
  highlights: HighlightForRender[];
  onLayout: (report: LayoutReport) => void;
};

const isKatex = (el: Element): boolean => {
  const cls = el.properties?.className;
  const list = Array.isArray(cls) ? cls : typeof cls === "string" ? cls.split(/\s+/) : [];
  return list.some((c) => typeof c === "string" && c.startsWith("katex"));
};

type Entry = { parent: Parent; index: number; node: Text; fragment: Fragment };

// hast-util-to-jsx-runtime (react-markdown's renderer) DROPS whitespace-only
// string children of these elements (a React nesting error otherwise), so no
// DOM text node exists for them. Mirror that here so hast order == DOM order.
const TABLE_ELEMENTS = new Set(["table", "tbody", "thead", "tfoot", "tr"]);
const isHtmlWhitespace = (s: string): boolean => s.replace(/[ \t\n\f\r]/g, "") === "";

function collect(tree: Root, source: string): Entry[] {
  const entries: Entry[] = [];
  const visit = (parent: Parent) => {
    parent.children.forEach((child, index) => {
      if (child.type === "element") {
        if (isKatex(child)) return; // whole subtree skipped, both here and in the DOM walk
        visit(child);
        return;
      }
      if (child.type !== "text") return;
      const value = child.value;
      if (value === "") return; // React renders no DOM node for an empty string
      if (
        parent.type === "element" &&
        TABLE_ELEMENTS.has((parent as Element).tagName) &&
        isHtmlWhitespace(value)
      ) {
        return; // dropped by the renderer, see TABLE_ELEMENTS
      }
      let fragment: Fragment = { value, starts: null, ends: null };
      const pos = child.position;
      // Inside <code> markdown decodes nothing: align literally.
      const literal = parent.type === "element" && (parent as Element).tagName === "code";
      if (pos?.start?.offset != null && pos.end?.offset != null && pos.end.offset > pos.start.offset) {
        const aligned = alignValueToSource(
          value,
          source.slice(pos.start.offset, pos.end.offset),
          pos.start.offset,
          { literal },
        );
        if (aligned) fragment = { value, ...aligned };
      } else if (
        parent.type === "element" &&
        (parent as Element).tagName === "code" &&
        parent.position?.start?.offset != null &&
        parent.position.end?.offset != null
      ) {
        // Fenced/indented code block: the text node has no position, the
        // <code> element spans fence to fence.
        const { src, offset } = codeBlockSlice(
          source.slice(parent.position.start.offset, parent.position.end.offset),
          parent.position.start.offset,
        );
        const aligned = alignValueToSource(value, src, offset, { literal: true });
        if (aligned) fragment = { value, ...aligned };
      }
      entries.push({ parent, index, node: child, fragment });
    });
  };
  visit(tree);
  return entries;
}

export function rehypeHighlights(options: RehypeHighlightsOptions) {
  const { source, highlights, onLayout } = options;
  return (tree: Root) => {
    const entries = collect(tree, source);
    const fragments = entries.map((e) => e.fragment);

    // Integrity check per stored highlight: rendered text at its range must be
    // exactly what was selected when it was made.
    const valid: HighlightForRender[] = [];
    const invalid: string[] = [];
    for (const h of highlights) {
      if (
        Number.isInteger(h.startOffset) &&
        Number.isInteger(h.endOffset) &&
        h.startOffset < h.endOffset &&
        renderedTextInRange(fragments, h.startOffset, h.endOffset) === h.selectedText
      ) {
        valid.push(h);
      } else {
        invalid.push(h.id);
      }
    }
    // Overlaps: the most recently created highlight wins for the characters
    // it covers. Sort ascending so a later entry overwrites an earlier one.
    valid.sort((a, b) => (a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0));

    const outFragments: Fragment[] = [];
    const replacements = new Map<Text, ElementContent[]>();

    for (const entry of entries) {
      const f = entry.fragment;
      if (!f.starts || !f.ends || valid.length === 0) {
        outFragments.push(f);
        continue;
      }
      // Top highlight per character.
      const top: (HighlightForRender | null)[] = new Array(f.value.length).fill(null);
      let any = false;
      for (const h of valid) {
        for (let i = 0; i < f.value.length; i += 1) {
          if (f.starts[i] >= h.startOffset && f.ends[i] <= h.endOffset) {
            top[i] = h;
            any = true;
          }
        }
      }
      if (!any) {
        outFragments.push(f);
        continue;
      }
      // Split into runs of equal top highlight.
      const nodes: ElementContent[] = [];
      let runStart = 0;
      for (let i = 1; i <= f.value.length; i += 1) {
        if (i === f.value.length || top[i] !== top[runStart]) {
          const value = f.value.slice(runStart, i);
          const piece: Fragment = {
            value,
            starts: f.starts.slice(runStart, i),
            ends: f.ends.slice(runStart, i),
          };
          outFragments.push(piece);
          const h = top[runStart];
          const textNode: Text = { type: "text", value };
          if (h) {
            const properties: Element["properties"] = {
              className: ["hl", `hl-${h.colour}`],
              dataHlId: h.id,
            };
            if (h.note) properties.title = h.note;
            nodes.push({ type: "element", tagName: "mark", properties, children: [textNode] });
          } else {
            nodes.push(textNode);
          }
          runStart = i;
        }
      }
      replacements.set(entry.node, nodes);
    }

    if (replacements.size > 0) {
      const parents = new Set<Parent>();
      for (const e of entries) if (replacements.has(e.node)) parents.add(e.parent);
      for (const parent of parents) {
        const next: ElementContent[] = [];
        for (const child of parent.children as ElementContent[]) {
          const rep = child.type === "text" ? replacements.get(child) : undefined;
          if (rep) next.push(...rep);
          else next.push(child);
        }
        parent.children = next as Parent["children"];
      }
    }

    onLayout({ fragments: outFragments, invalid });
  };
}
