"""
Step 4 of the worker: pull structured facts out of the text, and enforce
export-control markings.

Engineering documents follow naming conventions (KPS-1001, Rev F, KPC-10442),
so regular expressions get us most of the way for the pilot.
"""

import re
from dataclasses import dataclass, field

# Lookarounds instead of \b: in regex "_" counts as a word character, so \b would
# NOT match "QA-0099" inside the file name "QA-0099_RevA_...".
DOC_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])((?:KPS|QA|WI|SUP)-\d{3,5})(?!\d)")
REVISION_RE = re.compile(r"\bRev(?:ision)?\.?\s+([A-Z])\b")          # case-sensitive on purpose
PART_NUMBER_RE = re.compile(r"\bKPC-\d{5}\b")
STATUS_LINE_RE = re.compile(r"Status:\s*(Released|Superseded|Draft)", re.IGNORECASE)
STATUS_WORD_RE = re.compile(r"\b(Released|Superseded|Draft)\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Export control: your answer, as code. The marking travels with the document,
# so we trust the CONTENT over the folder it happens to sit in.
# ---------------------------------------------------------------------------
ITAR_RE = re.compile(
    r"\bITAR\b"                      # word boundary: "ITAR", not "CAPITAR"
    r"|\bEXPORT[\s-]+CONTROLLED\b"
    r"|\b22\s*CFR\s*12[0-9]"         # 22 CFR 120-130, the ITAR citation
    r"|\bUSML\b",                    # U.S. Munitions List
    re.IGNORECASE,
)
ITAR_GROUP = "ITAR-Authorized"


def apply_export_control(text: str, groups: list[str]) -> tuple[list[str], bool, str | None]:
    """
    If the text carries an export-control marking, REPLACE the groups with
    ITAR-Authorized only. Appending would leave 'Everyone' in the list, and RLS
    grants access when ANY group matches.

    Returns (groups, is_itar, matched_phrase).
    """
    match = ITAR_RE.search(text)
    if not match:
        return groups, False, None
    return [ITAR_GROUP], True, match.group(0)


@dataclass
class DocMeta:
    doc_number: str
    revision: str
    title: str
    part_numbers: list[str] = field(default_factory=list)
    release_status: str = "Unknown"


def extract_metadata(text: str, filename_stem: str) -> DocMeta:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # A document's own number is in its file name or its first line. Searching the
    # whole text would pick up OTHER documents it merely references ("per KPS-7700").
    m = DOC_NUMBER_RE.search(filename_stem) or (DOC_NUMBER_RE.search(lines[0]) if lines else None)
    doc_number = m.group(1) if m else filename_stem          # fallback: the file name

    m = REVISION_RE.search(text)
    revision = m.group(1) if m else "-"                      # some docs carry no revision

    m = STATUS_LINE_RE.search(text) or STATUS_WORD_RE.search(text)
    status = m.group(1).capitalize() if m else "Unknown"

    parts = sorted(set(PART_NUMBER_RE.findall(text)))

    # Title: the first line that contains the doc number and says something more
    title = lines[0] if lines else filename_stem
    for ln in lines:
        if doc_number in ln:
            rest = ln.replace(doc_number, "").strip(" -:")
            if len(rest) >= 12:                             # "Supplier" alone is not a title
                title = rest
                break

    return DocMeta(doc_number, revision, title[:200], parts, status)
