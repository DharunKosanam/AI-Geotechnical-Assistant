"""Web-ingest extraction (Phase 2): chrome stripping, structure preservation,
table rendering via the shared v3-xlsx row renderer, JS-shell warning."""
from app.services.web_extract import extract_web_page, LOW_TEXT_RATIO


PAGE = """
<html><head><title>Travel funding - Graduate Studies - UVic</title>
<style>.menu{color:red}</style><script>var x=1;</script></head>
<body>
<div id="cookie-banner">We use cookies. Accept?</div>
<header><a href="/">UVic home</a></header>
<nav><ul><li>Admissions</li><li>Programs</li><li>Research</li></ul></nav>
<div class="breadcrumbs">Home &gt; Graduate Studies &gt; Finances</div>
<main>
  <h1>Travel funding</h1>
  <p>Awards support travel to <a href="/conf">conferences</a>.</p>
  <h2>Eligibility</h2>
  <ul>
    <li>registered full-time</li>
    <li>no outstanding fees
      <ul><li>check your account</li></ul>
    </li>
  </ul>
  <h2>Amounts</h2>
  <table>
    <caption>Award amounts</caption>
    <tr><th>Destination</th><th>Maximum</th></tr>
    <tr><td>Outside BC</td><td>$600</td></tr>
    <tr><td>Within BC</td><td>$400</td></tr>
  </table>
  <ol><li>Apply by email.</li><li>Attach receipts.</li></ol>
  <div>Bare text in a container survives.</div>
</main>
<footer>Legal | Privacy | Sitemap</footer>
</body></html>
"""


def test_extracts_structure_and_strips_chrome():
    r = extract_web_page(PAGE)
    assert r.title == "Travel funding - Graduate Studies - UVic"
    # Structure preserved.
    assert "# Travel funding" in r.text
    assert "## Eligibility" in r.text
    assert "- registered full-time" in r.text
    assert "  - check your account" in r.text        # nested list, indented
    assert "1. Apply by email." in r.text
    assert "Bare text in a container survives." in r.text
    # Chrome gone.
    for junk in ("Admissions", "cookies", "UVic home", "Sitemap", "Home >"):
        assert junk not in r.text, junk
    assert "var x=1" not in r.text and ".menu" not in r.text
    assert r.headings == 3 and r.list_items == 5


def test_table_rendered_by_shared_row_renderer():
    r = extract_web_page(PAGE)
    assert r.tables == 1
    # The v3-xlsx renderer's shape: kind + name header, then one row per line
    # with the header row restated.
    assert "## Table: Award amounts" in r.text
    assert "| Destination | Maximum |" in r.text
    assert "| Outside BC | $600 |" in r.text
    assert "| Within BC | $400 |" in r.text


def test_main_content_preferred_over_body():
    html = ("<html><head><title>t</title></head><body>"
            "<div>outside main text that should not appear</div>"
            "<main><p>inside main</p></main></body></html>")
    r = extract_web_page(html)
    assert "inside main" in r.text
    assert "outside main" not in r.text


def test_js_shell_gets_ratio_warning():
    filler = "<script>" + "x" * 60_000 + "</script>"
    html = f"<html><head><title>App</title></head><body>{filler}<div id='root'>Loading…</div></body></html>"
    r = extract_web_page(html)
    assert r.char_count < 100
    assert r.text_ratio < LOW_TEXT_RATIO
    assert any("JavaScript" in w for w in r.warnings)


def test_empty_page_warns_never_raises():
    r = extract_web_page("")
    assert r.char_count == 0 and r.warnings
    r2 = extract_web_page("<html><body></body></html>")
    assert r2.char_count == 0 and r2.warnings


def test_role_and_aria_hidden_stripped():
    html = ("<html><head><title>t</title></head><body><main>"
            "<div role='navigation'>Menu A | Menu B</div>"
            "<span aria-hidden='true'>&#9654;</span>"
            "<p>Real content here.</p></main></body></html>")
    r = extract_web_page(html)
    assert "Real content here." in r.text
    assert "Menu A" not in r.text
