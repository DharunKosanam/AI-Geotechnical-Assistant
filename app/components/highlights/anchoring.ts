/**
 * Highlight anchoring: pure functions, no React, no DOM.
 *
 * A highlight is stored as UTF-16 offsets into the SOURCE markdown of an
 * assistant message (write-once on the server). Selections, however, happen
 * in the RENDERED DOM. The bridge is the position information remark keeps on
 * every text node: `position.start.offset / end.offset` are offsets into the
 * source of exactly that node's span. Within one span the rendered value can
 * still differ from the raw slice -- markdown decodes `\*` and `&amp;`, trims
 * whitespace around soft line breaks, drops `> ` continuation markers and code
 * delimiters -- so `alignValueToSource` walks value and slice together and
 * produces, per rendered character, the source range it came from. When it
 * cannot do that without guessing it returns null and the node is treated as
 * unmappable: an endpoint that lands in it is REFUSED (nothing happens); a
 * node strictly inside a selection is simply skipped.
 *
 * Everything the rehype plugin and the selection handler need is expressed on
 * a flat list of `Fragment`s -- the message's rendered text nodes in document
 * order, each carrying its per-character source ranges (or null). Both the
 * DOM walk (TreeWalker over text nodes, KaTeX subtrees skipped) and the hast
 * walk (text nodes, KaTeX subtrees skipped) produce the same sequence, and the
 * selection handler verifies that node-by-node before trusting an index.
 */

export type Fragment = {
  /** Rendered text of this DOM text node. */
  value: string;
  /** Absolute source offset where each character starts, or null if unmappable. */
  starts: number[] | null;
  /** Absolute source offset just past each character, or null if unmappable. */
  ends: number[] | null;
};

export type SourceRange = { start: number; end: number; selectedText: string };

// Named character references micromark decodes that a model answer might
// plausibly contain. Anything else fails alignment (-> unmappable node), which
// is a refusal, never a wrong position.
const NAMED_ENTITIES: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
  deg: "°", plusmn: "±", times: "×", divide: "÷", micro: "µ", middot: "·",
  hellip: "…", ndash: "–", mdash: "—", lsquo: "‘", rsquo: "’", ldquo: "“",
  rdquo: "”", copy: "©", reg: "®", trade: "™", laquo: "«", raquo: "»",
  bull: "•", sup2: "²", sup3: "³", frac12: "½", frac14: "¼", frac34: "¾",
  minus: "−", le: "≤", ge: "≥", ne: "≠", approx: "≈", infin: "∞", radic: "√",
  sum: "∑", prod: "∏", int: "∫", part: "∂", nabla: "∇",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", zeta: "ζ",
  eta: "η", theta: "θ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν",
  xi: "ξ", pi: "π", rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ",
  chi: "χ", psi: "ψ", omega: "ω", Gamma: "Γ", Delta: "Δ", Theta: "Θ",
  Lambda: "Λ", Xi: "Ξ", Pi: "Π", Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

const isWs = (ch: string | undefined): boolean =>
  ch === " " || ch === "\t" || ch === "\n" || ch === "\r";

/** Decode the character reference starting at src[j] === "&". */
function decodeEntity(src: string, j: number): { ch: string; len: number } | null {
  const semi = src.indexOf(";", j + 1);
  if (semi < 0 || semi - j > 32) return null;
  const body = src.slice(j + 1, semi);
  let ch: string | undefined;
  if (body[0] === "#") {
    const hex = body[1] === "x" || body[1] === "X";
    const digits = body.slice(hex ? 2 : 1);
    if (!/^[0-9a-fA-F]+$/.test(digits) || (!hex && !/^[0-9]+$/.test(digits))) return null;
    const n = parseInt(digits, hex ? 16 : 10);
    if (!Number.isFinite(n) || n <= 0 || n > 0x10ffff) return null;
    ch = String.fromCodePoint(n);
  } else {
    ch = NAMED_ENTITIES[body];
  }
  if (ch === undefined) return null;
  return { ch, len: semi - j + 1 };
}

/**
 * Align a rendered text-node value with the raw source slice it came from.
 *
 * Returns per-character absolute source ranges [starts[i], ends[i]), or null
 * when some rendered character cannot be located in the slice by these rules
 * alone:
 *   1. identical character            -> 1:1
 *   2. whitespace vs whitespace run    -> the value's whitespace run absorbs the
 *                                         source's (soft-break trimming, code
 *                                         line-ending -> space)
 *   3. source-only whitespace          -> skipped; right after a newline the
 *      (and "> " quote markers)           blockquote continuation marker too
 *   4. backslash escape  \x  -> x
 *   5. character reference &amp; -> &
 *   6. leading code-span backticks    -> skipped (only before the first char)
 * `srcOffset` is the absolute source offset of `src[0]`. In `literal` mode
 * (code spans and code blocks, where markdown decodes nothing) rules 4 and 5
 * are off, so "&amp;" and "\*" inside code stay 1:1.
 */
export function alignValueToSource(
  value: string,
  src: string,
  srcOffset: number,
  options: { literal?: boolean } = {},
): { starts: number[]; ends: number[] } | null {
  const literal = options.literal === true;
  const starts: number[] = new Array(value.length);
  const ends: number[] = new Array(value.length);
  let i = 0;
  let j = 0;
  let afterNewline = false;

  while (i < value.length) {
    const c = value[i];
    if (j >= src.length) {
      // Only a final newline may outrun the source: mdast-util-to-hast appends
      // "\n" to a code block's text, and an unterminated fence has no source
      // newline for it. It maps to a zero-width range at the end.
      if (c === "\n" && i === value.length - 1) {
        starts[i] = srcOffset + j;
        ends[i] = srcOffset + j;
        i += 1;
        continue;
      }
      return null;
    }
    const s = src[j];

    if (!literal && s === "&") {
      // A decodable character reference wins over a literal "&" so that
      // "&amp;" -> "&" consumes the whole reference. A bare "&" that is not
      // a valid reference falls through to the identical-character rule.
      const ent = decodeEntity(src, j);
      if (ent && ent.ch[0] === c) {
        starts[i] = srcOffset + j;
        ends[i] = srcOffset + j + ent.len;
        if (ent.ch.length === 2) {
          // An astral reference decodes to two UTF-16 units; both map onto it.
          if (value[i + 1] !== ent.ch[1]) return null;
          starts[i + 1] = srcOffset + j + ent.len;
          ends[i + 1] = srcOffset + j + ent.len;
          i += 1;
        }
        afterNewline = false;
        i += 1;
        j += ent.len;
        continue;
      }
    }

    if (s === c) {
      starts[i] = srcOffset + j;
      ends[i] = srcOffset + j + 1;
      afterNewline = c === "\n";
      i += 1;
      j += 1;
      continue;
    }

    if (isWs(c) && isWs(s)) {
      // Whitespace runs on both sides: pair them up 1:1 and let the LAST value
      // whitespace char absorb whatever source whitespace is left over.
      let vEnd = i;
      while (vEnd < value.length && isWs(value[vEnd])) vEnd += 1;
      let sEnd = j;
      while (sEnd < src.length && isWs(src[sEnd])) sEnd += 1;
      const vLen = vEnd - i;
      const sLen = sEnd - j;
      if (sLen < vLen) return null;
      for (let k = 0; k < vLen; k += 1) {
        starts[i + k] = srcOffset + j + k;
        ends[i + k] = k === vLen - 1 ? srcOffset + sEnd : srcOffset + j + k + 1;
      }
      afterNewline = src.slice(j, sEnd).includes("\n");
      i = vEnd;
      j = sEnd;
      continue;
    }

    if (isWs(s)) {
      // Source-only whitespace (indentation of a continuation line, trailing
      // spaces before a soft break, padding inside a code span).
      if (s === "\n") afterNewline = true;
      j += 1;
      continue;
    }

    if (afterNewline && s === ">") {
      // Blockquote continuation marker ("> " at the start of a source line).
      j += 1;
      continue;
    }

    if (!literal && s === "\\" && j + 1 < src.length && src[j + 1] === c) {
      starts[i] = srcOffset + j;
      ends[i] = srcOffset + j + 2;
      afterNewline = false;
      i += 1;
      j += 2;
      continue;
    }

    if (i === 0 && s === "`") {
      j += 1;
      continue;
    }

    return null;
  }
  return { starts, ends };
}

/**
 * Source slice for a fenced/indented code block's text node, which has no
 * position of its own: strip the fence lines from the enclosing element's
 * source. Returns the slice and its absolute offset.
 */
export function codeBlockSlice(elementSrc: string, elementOffset: number): { src: string; offset: number } {
  const fence = /^([ \t]*)(`{3,}|~{3,})[^\n]*\n/.exec(elementSrc);
  if (!fence) return { src: elementSrc, offset: elementOffset }; // indented code block
  let body = elementSrc.slice(fence[0].length);
  const offset = elementOffset + fence[0].length;
  // Strip the closing fence line but KEEP the newline before it: the rendered
  // value ends with "\n" (appended by mdast-util-to-hast) and that newline is
  // the source it aligns to.
  const closing = new RegExp(`(^|\\n)[ \\t]*${fence[2][0]}{${fence[2].length},}[ \\t]*$`);
  const m = closing.exec(body);
  if (m) body = body.slice(0, m.index + m[1].length);
  return { src: body, offset };
}

/** Whitespace-only text that carries no rendered characters (layout "\n"). */
export const isLayoutWhitespace = (f: Fragment): boolean =>
  f.starts === null && f.value.trim() === "";

/**
 * Rendered text of every mappable character whose source range lies inside
 * [start, end). This is the render-time integrity check: it must equal the
 * stored selectedText or the highlight is not drawn.
 */
export function renderedTextInRange(fragments: Fragment[], start: number, end: number): string {
  let out = "";
  for (const f of fragments) {
    if (!f.starts || !f.ends) continue;
    for (let i = 0; i < f.value.length; i += 1) {
      if (f.starts[i] >= start && f.ends[i] <= end) out += f.value[i];
    }
  }
  return out;
}

export type FragmentPosition = { fragment: number; offset: number };

/**
 * Turn a selection expressed as fragment positions (start inclusive, end
 * exclusive, document order) into a source range, or return null to REFUSE.
 *
 * Refused: an endpoint inside a non-whitespace unmappable fragment (math,
 * footnote labels, anything alignment gave up on), or a selection with no
 * mappable rendered character. Endpoints inside pure layout whitespace snap to
 * the nearest mappable character on the inside -- that whitespace has no
 * rendered extent, so no position is being guessed.
 */
export function positionsToSourceRange(
  fragments: Fragment[],
  from: FragmentPosition,
  to: FragmentPosition,
): SourceRange | null {
  const startF = fragments[from.fragment];
  const endF = fragments[to.fragment];
  if (!startF || !endF) return null;
  if (startF.starts === null && !isLayoutWhitespace(startF)) return null;
  if (endF.starts === null && !isLayoutWhitespace(endF)) return null;
  if (from.fragment > to.fragment || (from.fragment === to.fragment && from.offset >= to.offset)) {
    return null;
  }

  let start = -1;
  let end = -1;
  let text = "";
  for (let k = from.fragment; k <= to.fragment; k += 1) {
    const f = fragments[k];
    if (!f.starts || !f.ends) continue;
    const lo = k === from.fragment ? from.offset : 0;
    const hi = k === to.fragment ? to.offset : f.value.length;
    for (let i = lo; i < hi; i += 1) {
      if (start < 0) start = f.starts[i];
      end = f.ends[i];
      text += f.value[i];
    }
  }
  if (start < 0 || end <= start || text.trim() === "") return null;
  return { start, end, selectedText: text };
}
