"""Document-level classification"""

from __future__ import annotations

from tpd.classify.typology_clf import classify_document
from tpd.extract import parse_html

# A CCPA opt-out page in the shape that hid website__durect-com's disclosure:
# the policy text sits in bare <div>s while template filler occupies the <p>s.
_FILLER = "<p>" + "Lorem ipsum dolor sit amet consectetur adipisicing elit. " * 6 + "</p>"
DO_NOT_SELL_HTML = f"""
<html><head><title>Communication Preferences</title></head><body>
<h2>Do Not Sell My Personal Information</h2>
{_FILLER}{_FILLER}{_FILLER}
<div><div>At Example Inc., we respect your right to opt-out of the sale of your
personal information.</div>
<div>As permitted by applicable law, we may sell your personal information.</div>
<div>California residents can exercise the right to opt out by submitting this
form, and we will honor your privacy choices under the CCPA.</div></div>
</body></html>
"""


def test_ccpa_sale_page_is_relevant_generic():
    doc = parse_html(DO_NOT_SELL_HTML)
    dc = classify_document(doc, role="do_not_sell", target_type="website",
                           doc_id="do_not_sell-00", first_party={"example"})
    assert dc.medium == "prose"
    assert dc.relevant
    assert "prose:generic" in dc.facets
