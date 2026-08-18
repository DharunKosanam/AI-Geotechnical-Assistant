/**
 * DOM side of anchoring: walk a rendered assistant body's text nodes in the
 * same order the rehype plugin walked hast (KaTeX subtrees skipped), verify
 * the two agree node by node, and turn a browser Selection into fragment
 * positions -> a source range. Any doubt is a refusal (null), never a guess.
 */
import {
  positionsToSourceRange,
  type Fragment,
  type FragmentPosition,
  type SourceRange,
} from "./anchoring";

const isKatexElement = (el: Element): boolean =>
  Array.from(el.classList).some((c) => c.startsWith("katex"));

/** Text nodes of `container` in document order, skipping KaTeX subtrees. */
export function collectTextNodes(container: HTMLElement): Text[] {
  const doc = container.ownerDocument;
  const walker = doc.createTreeWalker(container, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        return isKatexElement(node as Element) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
      }
      return (node as Text).data === "" ? NodeFilter.FILTER_SKIP : NodeFilter.FILTER_ACCEPT;
    },
  });
  const out: Text[] = [];
  let n = walker.nextNode();
  while (n) {
    out.push(n as Text);
    n = walker.nextNode();
  }
  return out;
}

/** The plugin's fragment list must describe exactly these DOM text nodes. */
export function layoutMatchesDom(nodes: Text[], fragments: Fragment[]): boolean {
  if (nodes.length !== fragments.length) return false;
  for (let i = 0; i < nodes.length; i += 1) {
    if (nodes[i].data !== fragments[i].value) return false;
  }
  return true;
}

function firstTextIn(node: Node): Text | null {
  if (node.nodeType === Node.TEXT_NODE) return node as Text;
  const w = node.ownerDocument!.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  return w.nextNode() as Text | null;
}

function lastTextIn(node: Node): Text | null {
  if (node.nodeType === Node.TEXT_NODE) return node as Text;
  const w = node.ownerDocument!.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  let last: Text | null = null;
  let n = w.nextNode();
  while (n) {
    last = n as Text;
    n = w.nextNode();
  }
  return last;
}

/**
 * Resolve one selection boundary to (fragment index, char offset).
 *  - text node: itself (must be one of the walked nodes -> not KaTeX)
 *  - element + offset: the leaf on the INSIDE of the selection must be one of
 *    the walked text nodes; an <img> (no text) or KaTeX leaf -> null (refuse)
 */
export function resolveBoundary(
  nodes: Text[],
  node: Node,
  offset: number,
  side: "start" | "end",
): FragmentPosition | null {
  if (node.nodeType === Node.TEXT_NODE) {
    const idx = nodes.indexOf(node as Text);
    return idx < 0 ? null : { fragment: idx, offset };
  }
  if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return null;
  const children = node.childNodes;
  if (side === "start") {
    if (offset < children.length) {
      const leaf = firstTextIn(children[offset]);
      if (!leaf) return null;
      const idx = nodes.indexOf(leaf);
      return idx < 0 ? null : { fragment: idx, offset: 0 };
    }
    // After the last child: the next walked text node following this element.
    for (let i = 0; i < nodes.length; i += 1) {
      const rel = node.compareDocumentPosition(nodes[i]);
      if (rel & Node.DOCUMENT_POSITION_FOLLOWING && !(rel & Node.DOCUMENT_POSITION_CONTAINED_BY)) {
        return { fragment: i, offset: 0 };
      }
    }
    return null;
  }
  if (offset > 0) {
    const leaf = lastTextIn(children[offset - 1]);
    if (!leaf) return null;
    const idx = nodes.indexOf(leaf);
    return idx < 0 ? null : { fragment: idx, offset: leaf.data.length };
  }
  // Before the first child: the last walked text node preceding this element.
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    const rel = node.compareDocumentPosition(nodes[i]);
    if (rel & Node.DOCUMENT_POSITION_PRECEDING && !(rel & Node.DOCUMENT_POSITION_CONTAINED_BY)) {
      return { fragment: i, offset: nodes[i].data.length };
    }
  }
  return null;
}

export type ResolvedSelection = { range: SourceRange; rect: DOMRect | null };

/**
 * Selection -> source range for the assistant body `container`, or null when
 * the selection is collapsed, outside the body, straddles KaTeX/an image, hits
 * an unmappable text node, or the DOM no longer matches the plugin's layout.
 * `reason` (when provided) receives a short machine-readable refusal cause.
 */
export function resolveDomSelection(
  container: HTMLElement,
  selection: Selection | null,
  fragments: Fragment[],
  reason?: (why: string) => void,
): ResolvedSelection | null {
  const why = reason || (() => {});
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    why("collapsed");
    return null;
  }
  const range = selection.getRangeAt(0);
  if (!container.contains(range.startContainer) || !container.contains(range.endContainer)) {
    why("outside");
    return null;
  }
  const nodes = collectTextNodes(container);
  if (!layoutMatchesDom(nodes, fragments)) {
    why("layout-mismatch");
    return null;
  }
  const from = resolveBoundary(nodes, range.startContainer, range.startOffset, "start");
  const to = resolveBoundary(nodes, range.endContainer, range.endOffset, "end");
  if (!from || !to) {
    why("boundary");
    return null;
  }
  const src = positionsToSourceRange(fragments, from, to);
  if (!src) {
    why("unmappable");
    return null;
  }
  const rect =
    typeof range.getBoundingClientRect === "function" ? range.getBoundingClientRect() : null;
  return { range: src, rect };
}
