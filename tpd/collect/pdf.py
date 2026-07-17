"""PDF capture."""

from __future__ import annotations

import html
import io

# PDFs beyond this size are not converted.
MAX_PDF_BYTES = 20_000_000

_PDF_MAGIC = b"%PDF-"


def looks_like_pdf(content_type: str = "", url: str = "", head: bytes = b"") -> bool:
    """Whether a response is (probably) a PDF document."""
    if head[:5] == _PDF_MAGIC:
        return True
    if "pdf" in (content_type or "").lower():
        return True
    path = (url or "").split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".pdf")


def pdf_to_html(data: bytes, title: str = "") -> str:
    """The extracted text of a PDF as minimal HTML ('' if extraction fails)."""
    if not data or len(data) > MAX_PDF_BYTES:
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception:  # noqa: BLE001 - malformed PDFs abound
        return ""
    text = "\n".join(pages).strip()
    if not text:
        return ""
    # Reflow the PDF's lines
    from ..extract import _segments_from_text

    body = "\n".join(f"<p>{html.escape(seg)}</p>" for seg in _segments_from_text(text))
    head_html = f"<head><title>{html.escape(title)}</title></head>" if title else "<head></head>"
    return f"<html>{head_html}<body>\n{body}\n</body></html>"
