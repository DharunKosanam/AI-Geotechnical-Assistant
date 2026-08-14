"""draw.io XML -> flat text extractor (Phase 2, DIAGRAM_EDITOR_ENABLED).

Required behaviors:
  1. simple flowchart -> exact output shape (title, nodes, connections);
  2. grouped container: children referencing a group via parent= do NOT vanish;
  3. unlabeled node emits "(unlabeled)" -- never silently dropped;
  4. zero vertices raises EmptyDiagramError (caller rejects the upload);
  5. edge with no label renders without a "(label: ...)" suffix;
  6. HTML-formatted labels are stripped to plain text;
  7. edge source/target that resolves to no cell surfaces as "(missing: id)",
     and an edge missing the attribute entirely as "(unconnected)";
  8. two nodes with IDENTICAL labels: edges resolve by cell ID, so each
     connection lands on the right node and neither node is deduped away;
  9. self-loop edge (source == target) renders as "A -> A".

Shape parsing (every node line carries "[<shape>]"):
 10. ellipse and rhombus style tokens map to plain English (rhombus -> diamond);
 11. no style attribute -- and a style of only key=value pairs -- default to
     rectangle (draw.io's implicit default);
 12. a custom mxgraph.* stencil emits the RAW token, never dropped;
 13. an unlabeled node still carries its shape: "(unlabeled) [ellipse]";
 14. an explicit shape= entry wins over the leading bare token
     ("ellipse;shape=doubleEllipse" is a double ellipse).

Extras beyond the required list (both real draw.io output forms):
  - legacy compressed <diagram> payload (base64 + raw deflate + URL-encoding);
  - <object>-wrapped cells whose label/id live on the wrapper.

Deterministic: pure parsing, no LLM, no network, no DB.
"""

import base64
import zlib
from urllib.parse import quote

import pytest

from app.services.diagram_extraction import EmptyDiagramError, extract_diagram_text

pytestmark = pytest.mark.unit


def _mxfile(cells: str, name: str = "Page-1") -> str:
    return (
        f'<mxfile host="embed.diagrams.net"><diagram id="d1" name="{name}">'
        f"<mxGraphModel><root>"
        f'<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        f"</root></mxGraphModel></diagram></mxfile>"
    )


def test_simple_flowchart_exact_output():
    xml = _mxfile(
        '<mxCell id="2" value="Borehole" vertex="1" parent="1"/>'
        '<mxCell id="3" value="Lab" vertex="1" parent="1"/>'
        '<mxCell id="4" value="Report" vertex="1" parent="1"/>'
        '<mxCell id="5" value="" edge="1" parent="1" source="2" target="3"/>'
        '<mxCell id="6" value="samples" edge="1" parent="1" source="3" target="4"/>',
        name="Site workflow",
    )
    assert extract_diagram_text(xml) == (
        "Diagram: Site workflow\n"
        "Nodes: Borehole [rectangle]; Lab [rectangle]; Report [rectangle]\n"
        "Connections:\n"
        "  Borehole -> Lab\n"
        "  Lab -> Report (label: samples)"
    )


def test_grouped_children_do_not_vanish():
    # Grouping in mxGraph is FLAT: children point at the group via parent=.
    xml = _mxfile(
        '<mxCell id="g1" value="Subsystem" vertex="1" style="group" parent="1"/>'
        '<mxCell id="2" value="Pump" vertex="1" parent="g1"/>'
        '<mxCell id="3" value="Valve" vertex="1" parent="g1"/>'
        '<mxCell id="4" edge="1" parent="g1" source="2" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Subsystem [group]; Pump [rectangle]; Valve [rectangle]" in out
    assert "  Pump -> Valve" in out


def test_unlabeled_node_emits_placeholder():
    xml = _mxfile(
        '<mxCell id="2" value="Start" vertex="1" parent="1"/>'
        '<mxCell id="3" vertex="1" parent="1"/>'
        '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Start [rectangle]; (unlabeled) [rectangle]" in out
    assert "  Start -> (unlabeled)" in out


def test_empty_diagram_raises_specific_exception():
    with pytest.raises(EmptyDiagramError) as exc:
        extract_diagram_text(_mxfile(""))
    # User-facing: this exact text lands on the failed attachment chip.
    assert "no shapes" in str(exc.value)
    # Edges alone (no vertices) are still an empty diagram.
    with pytest.raises(EmptyDiagramError):
        extract_diagram_text(
            _mxfile('<mxCell id="9" edge="1" parent="1" source="2" target="3"/>')
        )


def test_edge_with_no_label_has_no_label_suffix():
    xml = _mxfile(
        '<mxCell id="2" value="A" vertex="1" parent="1"/>'
        '<mxCell id="3" value="B" vertex="1" parent="1"/>'
        '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "  A -> B" in out
    assert "(label:" not in out


def test_html_labels_are_stripped():
    xml = _mxfile(
        '<mxCell id="2" value="&lt;b&gt;Sample&lt;/b&gt;&amp;nbsp;prep&lt;br&gt;stage"'
        ' vertex="1" parent="1"/>'
        '<mxCell id="3" value="&lt;i&gt;QA&lt;/i&gt;" vertex="1" parent="1"/>'
        '<mxCell id="4" value="&lt;font color=&quot;red&quot;&gt;fail&lt;/font&gt;"'
        ' edge="1" parent="1" source="2" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Sample prep stage [rectangle]; QA [rectangle]" in out
    assert "  Sample prep stage -> QA (label: fail)" in out


def test_unresolvable_and_absent_endpoints_surface_as_gaps():
    xml = _mxfile(
        '<mxCell id="2" value="A" vertex="1" parent="1"/>'
        '<mxCell id="4" edge="1" parent="1" source="2" target="99"/>'
        '<mxCell id="5" edge="1" parent="1" target="2"/>'
    )
    out = extract_diagram_text(xml)
    assert "  A -> (missing: 99)" in out
    assert "  (unconnected) -> A" in out


def test_identical_labels_resolve_by_id_and_are_not_deduped():
    # Two distinct "Pump" nodes: id=2 feeds the Tank, id=3 is fed by it.
    xml = _mxfile(
        '<mxCell id="2" value="Pump" vertex="1" parent="1"/>'
        '<mxCell id="3" value="Pump" vertex="1" parent="1"/>'
        '<mxCell id="4" value="Tank" vertex="1" parent="1"/>'
        '<mxCell id="5" edge="1" parent="1" source="2" target="4"/>'
        '<mxCell id="6" edge="1" parent="1" source="4" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Pump [rectangle]; Pump [rectangle]; Tank [rectangle]" in out  # both kept
    assert "  Pump -> Tank" in out
    assert "  Tank -> Pump" in out


def test_self_loop_edge():
    xml = _mxfile(
        '<mxCell id="2" value="Recirculate" vertex="1" parent="1"/>'
        '<mxCell id="3" edge="1" parent="1" source="2" target="2"/>'
    )
    assert "  Recirculate -> Recirculate" in extract_diagram_text(xml)


def test_object_wrapper_carries_id_and_label():
    # draw.io wraps cells in <object> when shapes carry custom data: the id
    # and label live on the WRAPPER, not the mxCell.
    xml = _mxfile(
        '<object id="2" label="Sensor"><mxCell vertex="1" parent="1"/></object>'
        '<mxCell id="3" value="Logger" vertex="1" parent="1"/>'
        '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Sensor [rectangle]; Logger [rectangle]" in out
    assert "  Sensor -> Logger" in out


def test_compressed_diagram_payload_inflates_to_same_output():
    inner = (
        "<mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="2" value="A" vertex="1" parent="1"/>'
        '<mxCell id="3" value="B" vertex="1" parent="1"/>'
        '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
        "</root></mxGraphModel>"
    )
    deflater = zlib.compressobj(wbits=-15)
    payload = base64.b64encode(
        deflater.compress(quote(inner, safe="").encode()) + deflater.flush()
    ).decode()
    xml = f'<mxfile><diagram id="d1" name="Zipped">{payload}</diagram></mxfile>'
    assert extract_diagram_text(xml) == (
        "Diagram: Zipped\nNodes: A [rectangle]; B [rectangle]\nConnections:\n  A -> B"
    )


def test_bare_mxgraphmodel_defaults_title_and_no_edges_says_none():
    xml = (
        "<mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="2" value="Lone" vertex="1" parent="1"/>'
        "</root></mxGraphModel>"
    )
    assert extract_diagram_text(xml) == (
        "Diagram: Untitled\nNodes: Lone [rectangle]\nConnections:\n  (none)"
    )


def test_non_diagram_xml_is_a_plain_valueerror():
    with pytest.raises(ValueError) as exc:
        extract_diagram_text("<html><body>nope</body></html>")
    assert not isinstance(exc.value, EmptyDiagramError)
    with pytest.raises(ValueError):
        extract_diagram_text("not xml at all")


# --- shape parsing ----------------------------------------------------------
def test_shape_ellipse_and_rhombus_map_to_plain_english():
    xml = _mxfile(
        '<mxCell id="2" value="Start" style="ellipse;whiteSpace=wrap;html=1;"'
        ' vertex="1" parent="1"/>'
        '<mxCell id="3" value="Approved?" style="rhombus;whiteSpace=wrap;html=1;"'
        ' vertex="1" parent="1"/>'
        '<mxCell id="4" edge="1" parent="1" source="2" target="3"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Start [ellipse]; Approved? [diamond]" in out
    # Shape rides the Nodes line only; connection endpoints stay bare labels.
    assert "  Start -> Approved?" in out


def test_no_style_and_keyvalue_only_style_default_to_rectangle():
    xml = _mxfile(
        '<mxCell id="2" value="Plain" vertex="1" parent="1"/>'
        '<mxCell id="3" value="Rounded" style="rounded=1;whiteSpace=wrap;html=1;"'
        ' vertex="1" parent="1"/>'
    )
    out = extract_diagram_text(xml)
    assert "Nodes: Plain [rectangle]; Rounded [rectangle]" in out


def test_custom_mxgraph_shape_emits_raw_token():
    xml = _mxfile(
        '<mxCell id="2" value="End"'
        ' style="strokeWidth=2;html=1;shape=mxgraph.flowchart.terminator;whiteSpace=wrap;"'
        ' vertex="1" parent="1"/>'
    )
    # Unknown/custom stencils surface as the raw token, never dropped.
    assert "Nodes: End [mxgraph.flowchart.terminator]" in extract_diagram_text(xml)


def test_unlabeled_node_still_carries_its_shape():
    xml = _mxfile(
        '<mxCell id="2" style="ellipse;whiteSpace=wrap;html=1;" vertex="1" parent="1"/>'
    )
    assert "Nodes: (unlabeled) [ellipse]" in extract_diagram_text(xml)


def test_explicit_shape_entry_wins_over_leading_bare_token():
    # draw.io's double ellipse is literally "ellipse;shape=doubleEllipse;...".
    xml = _mxfile(
        '<mxCell id="2" value="Stop" style="ellipse;shape=doubleEllipse;html=1;"'
        ' vertex="1" parent="1"/>'
        '<mxCell id="3" value="Store" style="shape=cylinder3;whiteSpace=wrap;"'
        ' vertex="1" parent="1"/>'
    )
    out = extract_diagram_text(xml)
    assert "Stop [double ellipse]" in out
    assert "Store [cylinder]" in out
