"""
Step 3 of the worker: turn a raw file into text, page by page.

Returns a list of (page_number, text) pairs. Page numbers are kept so answers
can cite "page 12". Word files have no fixed pages, so they come back as page None.
Excel files are NOT handled here: they go down the structured SQL path instead.
"""

import io
from pathlib import PurePosixPath

from docx import Document
from pypdf import PdfReader

# A PDF page with fewer characters than this is treated as a scanned image.
MIN_TEXT_CHARS = 50

Pages = list[tuple[int | None, str]]


def extract_pdf_text_layer(data: bytes) -> Pages:
    """Read the text layer that digital PDFs carry. Fast and free."""
    reader = PdfReader(io.BytesIO(data))
    return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]


def extract_docx(data: bytes) -> Pages:
    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return [(None, text)]


def needs_ocr(pages: Pages) -> bool:
    """True when the PDF has (almost) no text layer, i.e. it is a scan."""
    return sum(len(t.strip()) for _, t in pages) < MIN_TEXT_CHARS


def extract_text(key: str, data: bytes, ocr) -> tuple[Pages, str]:
    """
    Dispatch on file type. `ocr` is a function (key) -> Pages, injected by the
    worker so this module never calls AWS itself (which keeps it easy to test).

    Returns (pages, method) where method records how the text was obtained.
    """
    suffix = PurePosixPath(key).suffix.lower()

    if suffix == ".pdf":
        pages = extract_pdf_text_layer(data)
        if needs_ocr(pages):
            return ocr(key), "textract"
        return pages, "pdf-text-layer"

    if suffix == ".docx":
        return extract_docx(data), "docx"

    raise ValueError(f"Unsupported file type for text extraction: {suffix}")
