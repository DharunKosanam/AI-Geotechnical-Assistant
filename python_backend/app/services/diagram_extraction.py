"""Deterministic draw.io (mxGraphModel) XML -> flat text extraction.

Pure Python parsing with ZERO LLM calls: the flattened text is what gets
chunked and embedded so the model can reason about a drawn diagram's
structure. The PNG twin of the upload is display-only and never touches this
module (or any model).

Accepted inputs (all produced by the draw.io embed editor):
  - a bare <mxGraphModel> document;
  - an <mxfile> wrapper whose <diagram> holds a nested <mxGraphModel>;
  - an <mxfile> whose <diagram> holds the legacy compressed payload
    (base64 -> raw deflate -> URL-encoded XML) — stdlib-only to inflate.

Output shape (consumed as-is by the chunker):
    Diagram: <title or "Untitled">
    Nodes: A [rectangle]; B [diamond]; C [ellipse]
    Connections:
      A -> B
      B -> C (label: samples)

Rules the rest of the pipeline relies on:
  - EVERY vertex mxCell is a node, wherever it sits (groups are flat in
    mxGraph XML — children reference the group via parent=<id> — and
    <object>/<UserObject> wrappers carry the label for their inner cell), so
    grouped shapes never vanish.
  - Every node carries its shape in brackets — geometry is semantics in an
    engineering flowchart (rhombus = decision, ellipse = start/end). Common
    style tokens map to plain English, no token at all is draw.io's implicit
    rectangle, and unknown/custom tokens (mxgraph.* stencils) pass through
    raw rather than being dropped. Shape lives on the Nodes line only;
    connection endpoints stay bare labels.
  - Unlabeled nodes emit "(unlabeled)" rather than disappearing — with the
    shape attached, "(unlabeled) [ellipse]" still tells the model what kind
    of element the author drew.
  - Edges resolve source/target by cell ID to the node's label — duplicate
    labels cannot be confused because resolution never goes through the label.
  - Zero vertices raises EmptyDiagramError; the caller REJECTS the upload
    rather than indexing an empty document.
"""

import base64
import html
import re
import xml.etree.ElementTree as ET
import zlib
from typing import Dict, List, Optional
from urllib.parse import unquote


class EmptyDiagramError(ValueError):
    """The XML parsed cleanly but contains no vertex cells (no shapes).

    The message is user-facing: it is stored on the parent file doc verbatim
    and surfaces on the attachment chip, mirroring UnreadableDocumentError.
    """


# Matches any HTML/XML tag inside a draw.io rich-text label.
_TAG_RE = re.compile(r"<[^>]+>")
# Collapses whitespace runs (including the \xa0 that &nbsp; unescapes to).
_WS_RE = re.compile(r"[\s\xa0]+")

# Common draw.io shape tokens -> plain English. Anything not listed here is a
# custom/stencil shape and passes through as the raw token.
_SHAPE_NAMES = {
    "ellipse": "ellipse",
    "rhombus": "diamond",
    "triangle": "triangle",
    "hexagon": "hexagon",
    "cylinder": "cylinder",
    "cylinder3": "cylinder",
    "cloud": "cloud",
    "parallelogram": "parallelogram",
    "trapezoid": "trapezoid",
    "step": "step",
    "process": "process",
    "doubleEllipse": "double ellipse",
    "swimlane": "container",
    "group": "group",
    "text": "text",
    "actor": "actor",
    "note": "note",
    "card": "card",
    "callout": "callout",
}


def _shape_name(style: Optional[str]) -> str:
    """Plain-English shape for an mxCell style string.

    draw.io encodes geometry two ways: a leading bare token
    ("ellipse;whiteSpace=wrap;...") or an explicit shape= entry
    ("shape=cylinder;..."), and shape= WINS when both appear (draw.io's
    doubleEllipse is literally "ellipse;shape=doubleEllipse;..."). A style
    with neither — or no style at all — is draw.io's implicit default,
    rectangle ("rounded=0;whiteSpace=wrap;html=1;" is still a rectangle).
    """
    if style:
        parts = [p.strip() for p in style.split(";") if p.strip()]
        token = None
        for p in parts:
            if p.startswith("shape="):
                token = p[len("shape="):].strip()
                break
        if token is None and parts and "=" not in parts[0]:
            token = parts[0]
        if token:
            return _SHAPE_NAMES.get(token, token)
    return "rectangle"


def _clean_label(raw: Optional[str]) -> str:
    """Strip draw.io rich-text markup down to plain text.

    Labels are stored as HTML fragments ("<b>Start</b><br>here"). Tag
    boundaries become spaces so "<br>"-separated lines don't fuse into one
    word, entities are unescaped, and whitespace is collapsed.
    """
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _inflate_diagram_payload(data: str) -> str:
    """Legacy compressed <diagram> payload: base64 -> raw deflate -> URL-decode."""
    inflated = zlib.decompress(base64.b64decode(data), -15).decode("utf-8")
    return unquote(inflated)


def _find_graph_model(root: ET.Element) -> ET.Element:
    """Locate the <mxGraphModel> element for any accepted input form."""
    if root.tag == "mxGraphModel":
        return root
    if root.tag == "mxfile":
        diagram = root.find("diagram")
        if diagram is None:
            raise ValueError("Not a draw.io diagram: <mxfile> has no <diagram>.")
        nested = diagram.find("mxGraphModel")
        if nested is not None:
            return nested
        payload = (diagram.text or "").strip()
        if payload:
            try:
                return ET.fromstring(_inflate_diagram_payload(payload))
            except ET.ParseError as exc:
                raise ValueError(f"Not a draw.io diagram: {exc}") from exc
            except Exception as exc:
                raise ValueError(
                    "Not a draw.io diagram: could not decode the compressed "
                    f"<diagram> payload ({exc})."
                ) from exc
        raise ValueError("Not a draw.io diagram: <diagram> is empty.")
    raise ValueError(f"Not a draw.io diagram: unexpected root <{root.tag}>.")


def _diagram_title(root: ET.Element) -> str:
    """The <diagram name="..."> attribute, or "Untitled" when absent/blank."""
    if root.tag == "mxfile":
        diagram = root.find("diagram")
        if diagram is not None:
            name = _clean_label(diagram.get("name"))
            if name:
                return name
    return "Untitled"


def extract_diagram_text(xml_string: str) -> str:
    """Flatten a draw.io diagram to the deterministic text form above.

    Raises EmptyDiagramError for a shapeless diagram (caller rejects the
    upload) and ValueError for input that is not a draw.io document at all.
    """
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as exc:
        raise ValueError(f"Not a draw.io diagram: {exc}") from exc

    title = _diagram_title(root)
    model = _find_graph_model(root)

    # ElementTree has no parent pointers; the map recovers <object>/<UserObject>
    # wrappers, which hold the id and label their inner mxCell lacks.
    parent_of = {child: parent for parent in model.iter() for child in parent}

    nodes: List[str] = []          # display labels, document order, dupes kept
    label_by_id: Dict[str, str] = {}
    edges: List[ET.Element] = []

    # iter() walks the whole tree, so cells survive any nesting draw.io emits.
    for cell in model.iter("mxCell"):
        wrapper = parent_of.get(cell)
        wrapped = wrapper is not None and wrapper.tag in ("object", "UserObject")
        cell_id = cell.get("id") or (wrapper.get("id") if wrapped else None)
        raw_label = cell.get("value")
        if raw_label is None and wrapped:
            raw_label = wrapper.get("label")
        label = _clean_label(raw_label)

        if cell.get("vertex") == "1":
            display = label or "(unlabeled)"
            # Shape rides the Nodes line only; edges resolve to the bare
            # label so the Connections section stays readable.
            nodes.append(f"{display} [{_shape_name(cell.get('style'))}]")
            if cell_id is not None:
                label_by_id[cell_id] = display
        elif cell.get("edge") == "1":
            edges.append(cell)

    if not nodes:
        raise EmptyDiagramError(
            "The diagram has no shapes, so there is nothing to index. "
            "Add at least one shape and save again."
        )

    def endpoint(edge: ET.Element, attr: str) -> str:
        ref = edge.get(attr)
        if ref is None:
            return "(unconnected)"
        # Never silently dropped: an id that resolves to nothing is a gap the
        # model should be able to flag.
        return label_by_id.get(ref, f"(missing: {ref})")

    lines = [f"Diagram: {title}", f"Nodes: {'; '.join(nodes)}", "Connections:"]
    if edges:
        for edge in edges:
            line = f"  {endpoint(edge, 'source')} -> {endpoint(edge, 'target')}"
            edge_label = _clean_label(edge.get("value"))
            if edge_label:
                line += f" (label: {edge_label})"
            lines.append(line)
    else:
        lines.append("  (none)")
    return "\n".join(lines)
