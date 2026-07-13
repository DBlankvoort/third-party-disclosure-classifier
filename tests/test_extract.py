"""Validate tpd.extract.parse_html and DocTree."""

from __future__ import annotations

from tpd.extract import DocTree, NodeType, parse_html

POLICY_HTML = """
<html><head><title>Privacy Policy</title>
<script>var tracking = true;</script>
<style>p { color: red }</style>
</head><body>
<h1>Privacy Policy</h1>
<p>We share your data with advertising partners.</p>
<p>We share your data with advertising partners.</p>
<ul><li>Google Analytics</li></ul>
<a href="/cookie-policy">Cookie Policy</a>
<table>
  <tr><th>Provider</th><th>Purpose</th></tr>
  <tr><td>Hotjar</td><td>analytics</td></tr>
</table>
</body></html>
"""


class TestParseHtml:
    def test_title_and_text(self):
        doc = parse_html(POLICY_HTML)
        assert doc.title == "Privacy Policy"
        assert "advertising partners" in doc.text

    def test_script_and_style_dropped(self):
        doc = parse_html(POLICY_HTML)
        assert "tracking" not in doc.text
        assert all("color: red" not in s for s in doc.segments)

    def test_segments_from_block_tags_deduped(self):
        doc = parse_html(POLICY_HTML)
        shares = [s for s in doc.segments
                  if s == "We share your data with advertising partners."]
        assert len(shares) == 1  # adjacent duplicates collapse
        assert "Google Analytics" in doc.segments

    def test_links(self):
        doc = parse_html(POLICY_HTML)
        assert ("Cookie Policy", "/cookie-policy") in doc.links

    def test_tables(self):
        doc = parse_html(POLICY_HTML)
        (tbl,) = doc.tables
        assert tbl.headers == ["provider", "purpose"]
        assert tbl.n_rows == 2
        assert tbl.name_cells == ["Hotjar"]

    def test_empty_input(self):
        doc = parse_html("")
        assert doc.text == "" and doc.segments == [] and doc.tables == []

    def test_oversized_input_truncated(self):
        html = "<html><body><p>hello</p>" + "x" * 100 + "</body></html>"
        doc = parse_html(html, max_bytes=40)
        assert "hello" in doc.text

    def test_div_only_layout_falls_back_to_text_segments(self):
        # No block tags: segments must be rebuilt from rendered text.
        html = ("<html><body><div>We share data with partners.<br>"
                "We use cookies for analytics.</div></body></html>")
        doc = parse_html(html)
        assert any("share data with partners" in s for s in doc.segments)

    def test_identifier_cells_excluded_from_name_cells(self):
        html = """
        <table>
          <tr><th>Cookie</th><th>Provider</th></tr>
          <tr><td>_ga_123</td><td>Google</td></tr>
        </table>"""
        (tbl,) = parse_html(html).tables
        assert tbl.name_cells == ["Google"]


class TestDocTree:
    def test_headings_nest_and_parent_text(self):
        tree = DocTree.from_html(
            "<body><h1>Privacy</h1><h2>Sharing</h2>"
            "<p>We share data with partners.</p></body>")
        (text,) = tree.segments(NodeType.TEXT)
        assert text.parent.text == "Sharing"
        assert [a.text for a in text.ancestors()[:-1]] == ["Sharing", "Privacy"]

    def test_list_items_join_their_lead_in(self):
        tree = DocTree.from_html(
            "<body><p>We share data with:</p>"
            "<ul><li>advertisers</li><li>analytics providers</li></ul></body>")
        joined = [s.text for s in tree.sentences() if s.variant == 1]
        assert "We share data with: advertisers" in joined

    def test_inline_markup_does_not_split_sentences(self):
        tree = DocTree.from_html("<body><p>We <b>share</b> your data.</p></body>")
        assert [s.text for s in tree.sentences()] == ["We share your data."]

    def test_table_becomes_node_and_feeds_sentences(self):
        tree = DocTree.from_html(
            "<body><table><tr><th>Provider</th><th>Purpose</th></tr>"
            "<tr><td>Hotjar</td><td>Hotjar collects usage statistics.</td></tr>"
            "</table></body>")
        (node,) = tree.segments(NodeType.TABLE)
        assert node.table.name_cells == ["Hotjar"]
        assert node.table.rows == [["Hotjar", "Hotjar collects usage statistics."]]
        cell_sentences = {s.text for s in tree.sentences() if s.segment is node}
        assert cell_sentences == {"Hotjar collects usage statistics."}

    def test_boilerplate_kept_in_document_but_not_sentences(self):
        tree = DocTree.from_html(
            "<body><p>We share data with partners.</p>"
            "<footer><p>Follow us on Facebook.</p></footer></body>")
        doc = tree.as_document()
        assert any("Facebook" in s for s in doc.segments)
        assert "Facebook" in doc.text
        assert not any("Facebook" in s.text for s in tree.sentences())

    def test_from_text_bullets_and_sentences(self):
        tree = DocTree.from_text(
            "We collect data. We share it.\n- with advertisers\n")
        items = tree.segments(NodeType.LISTITEM)
        assert [n.text for n in items] == ["with advertisers"]
        texts = [n.text for n in tree.segments(NodeType.TEXT)]
        assert texts == ["We collect data.", "We share it."]
