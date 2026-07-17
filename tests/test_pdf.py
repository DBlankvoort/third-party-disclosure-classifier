"""PDF capture"""

from __future__ import annotations

import tpd.collect.base as base
from tpd.collect.base import fetch
from tpd.collect.pdf import looks_like_pdf, pdf_to_html


def _minimal_pdf(text: str) -> bytes:
    """A one-page uncompressed PDF showing ``text`` in Helvetica."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return out


class TestLooksLikePdf:
    def test_signals(self):
        assert looks_like_pdf(content_type="application/pdf")
        assert looks_like_pdf(content_type="Application/PDF; qs=0.001")
        assert looks_like_pdf(url="https://x.com/a/cookie-policy.pdf?v=2")
        assert looks_like_pdf(head=b"%PDF-1.7 ...")

    def test_non_pdf(self):
        assert not looks_like_pdf(content_type="text/html; charset=utf-8")
        assert not looks_like_pdf(url="https://x.com/privacy-policy")
        assert not looks_like_pdf(head=b"<!doctype html>")


class TestPdfToHtml:
    def test_extracts_text_as_html(self):
        html = pdf_to_html(_minimal_pdf("We may sell your personal information."),
                           title="Cookie Policy")
        assert "sell your personal information" in html
        assert "<title>Cookie Policy</title>" in html
        assert html.startswith("<html>")

    def test_garbage_and_empty_input(self):
        assert pdf_to_html(b"") == ""
        assert pdf_to_html(b"%PDF-1.4 not really a pdf") == ""


class TestFetchConvertsPdf:
    class _Resp:
        status_code = 200
        url = "https://x.com/cookie-policy.pdf"
        headers = {"Content-Type": "application/pdf"}
        content = _minimal_pdf("The cookie is provided by Google Analytics.")
        text = "binary-mojibake"

    def test_pdf_body_becomes_html_text(self, monkeypatch):
        monkeypatch.setattr(base._SESSION, "get",
                            lambda url, timeout, allow_redirects: self._Resp())
        res = fetch("https://x.com/cookie-policy.pdf")
        assert res.ok
        assert "Google Analytics" in res.text
        assert res.text.startswith("<html>")
