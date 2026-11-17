"""
Generate a fake Kestrel S: drive full of realistic test documents.

Every document is fictional (KPS = "Kestrel Process Spec"). The set is
designed to exercise each feature of the pipeline:

  * Two revisions of the same spec with DIFFERENT values  -> revision handling
  * A byte-identical stale copy in a project folder        -> SHA-256 dedupe
  * An image-only "scanned" PDF with a handwritten note    -> Textract OCR
  * A Word work instruction                                -> python-docx path
  * An Excel material-property table                       -> structured SQL path
  * An ITAR-marked spec, plus a copy misplaced in Shared/  -> marking scan override
  * A supplier datasheet only Procurement may see          -> permission filtering

Usage (from the repo root):
    python scripts/generate_sample_docs.py
"""

import shutil
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

ROOT = Path("sample-s-drive")
styles = getSampleStyleSheet()


def pdf(path: Path, title: str, header_lines: list[str], sections: list[tuple[str, str]],
        marking: str | None = None) -> None:
    """Write a text-based PDF spec. `marking` adds an export-control banner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    story = []
    if marking:
        story += [Paragraph(f"<b>{marking}</b>", styles["Normal"]), Spacer(1, 8)]
    story += [Paragraph(title, styles["Title"])]
    story += [Paragraph(line, styles["Normal"]) for line in header_lines]
    story += [Spacer(1, 12)]
    for heading, body in sections:
        story += [Paragraph(heading, styles["Heading2"]), Paragraph(body, styles["Normal"]), Spacer(1, 6)]
    if marking:
        story += [Spacer(1, 12), Paragraph(f"<b>{marking}</b>", styles["Normal"])]
    SimpleDocTemplate(str(path), pagesize=LETTER).build(story)


def heat_treat_spec(rev: str, age_temp: str, status: str) -> list[tuple[str, str]]:
    return [
        ("1. Scope", "This specification defines precipitation hardening of 17-4 PH stainless "
                     "steel machined components, part numbers KPC-10442 and KPC-10450."),
        ("2. Solution treatment", "Parts shall be solution treated (Condition A) at 1900 F "
                                  "plus or minus 25 F for 30 minutes, then air cooled below 90 F."),
        ("3. Aging", f"Age parts at {age_temp} plus or minus 10 F for 4 hours, then air cool. "
                     f"Resulting condition shall be recorded on the traveler."),
        ("4. Verification", "Hardness shall be verified per QA-0099 on one part per lot."),
        ("5. Revision status", f"Revision {rev}. Status: {status}."),
    ]


def scanned_pdf(path: Path) -> None:
    """Image-only PDF: no text layer, so pypdf finds nothing and Textract must OCR it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1700, 2200), (248, 246, 238))          # off-white paper
    d = ImageDraw.Draw(img)
    typed = ImageFont.load_default(size=34)
    big = ImageFont.load_default(size=52)
    note = ImageFont.load_default(size=40)
    lines = [
        ("QA-0099  HARDNESS INSPECTION PROCEDURE", big),
        ("Rev A   Issued 1998   Kestrel Precision Components", typed),
        ("", typed),
        ("1. Select one part per heat-treat lot.", typed),
        ("2. Test on a flat machined surface using a Rockwell C tester.", typed),
        ("3. Acceptance range for 17-4 PH, H1025: 35 to 42 HRC.", typed),
        ("4. Record lot number, reading and inspector on form QF-12.", typed),
    ]
    y = 160
    for text, font in lines:
        d.text((140, y), text, font=font, fill=(30, 30, 30))
        y += 80
    # A red "handwritten" margin note, the kind of redline Dana said matters most
    d.text((160, y + 120), "NOTE: retest if reading is within 1 HRC of the limit  - J.M. 2003",
           font=note, fill=(170, 20, 20))
    img = img.rotate(0.8, expand=False, fillcolor=(248, 246, 238))  # slightly crooked scan
    img.save(path, "PDF", resolution=200)


def work_instruction(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading("WI-0420 Final Inspection of KPC-10442 Brackets", level=1)
    doc.add_paragraph("Revision C. Owner: Quality Engineering.")
    for step in [
        "Verify the traveler shows heat treat per KPS-1001 Rev F.",
        "Measure bore diameter with a calibrated bore gauge: 12.700 mm +0.018 / -0.000.",
        "Check surface finish on the mounting face: Ra 1.6 micrometers maximum.",
        "Perform hardness check per QA-0099 and record the result on QF-12.",
        "Tag accepted parts green; quarantine rejects with an NCR number.",
    ]:
        doc.add_paragraph(step, style="List Number")
    doc.save(path)


def material_table(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Properties"
    ws.append(["material", "condition", "property", "value", "unit"])
    rows = [
        ("17-4 PH", "H900", "yield_strength", 170, "ksi"),
        ("17-4 PH", "H1025", "yield_strength", 145, "ksi"),
        ("17-4 PH", "H1150", "yield_strength", 105, "ksi"),
        ("17-4 PH", "H1025", "hardness", 38, "HRC"),
        ("Ti-6Al-4V", "Annealed", "yield_strength", 128, "ksi"),
        ("6061-T6", "T6", "yield_strength", 40, "ksi"),
    ]
    for r in rows:
        ws.append(r)
    wb.save(path)


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)

    # --- Two revisions of the same spec, with different aging temperatures ---
    rev_e = ROOT / "Engineering/Specs/KPS-1001_RevE_HeatTreat_17-4PH.pdf"
    pdf(rev_e, "KPS-1001 Heat Treatment of 17-4 PH Components",
        ["Kestrel Process Specification", "Revision E", "Superseded by Revision F"],
        heat_treat_spec("E", "1050 F", "Superseded"))

    pdf(ROOT / "Engineering/Specs/KPS-1001_RevF_HeatTreat_17-4PH.pdf",
        "KPS-1001 Heat Treatment of 17-4 PH Components",
        ["Kestrel Process Specification", "Revision F", "Released"],
        heat_treat_spec("F", "1025 F", "Released"))

    # --- The stale copy that caused the quality escape: byte-identical to Rev E ---
    stale = ROOT / "Engineering/Projects/2014/Customer_X/KPS-1001_FINAL_v3_REALLY_FINAL.pdf"
    stale.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(rev_e, stale)

    # --- Excel: structured data path ---
    material_table(ROOT / "Engineering/MaterialData/Material_Properties_Master.xlsx")

    # --- Word work instruction ---
    work_instruction(ROOT / "Quality/WorkInstructions/WI-0420_RevC_Final_Inspection.docx")

    # --- Scanned legacy procedure (OCR) ---
    scanned_pdf(ROOT / "Quality/Scans/QA-0099_RevA_Hardness_Inspection_1998_scan.pdf")

    # --- ITAR-controlled spec, plus a copy someone dropped in an open folder ---
    itar = ROOT / "Defense/ITAR/KPS-7700_RevB_Actuator_Bracket.pdf"
    marking = ("EXPORT CONTROLLED - ITAR. This document contains technical data subject to "
               "the International Traffic in Arms Regulations (22 CFR 120-130). "
               "Access limited to U.S. persons.")
    pdf(itar, "KPS-7700 Actuator Bracket Machining Requirements",
        ["Kestrel Process Specification", "Revision B", "Released", "Program: DEF-21"],
        [("1. Scope", "Machining and inspection requirements for actuator bracket KPC-77012."),
         ("2. Material", "Ti-6Al-4V annealed bar. Certs required per lot."),
         ("3. Critical dimensions", "Pivot bore 9.525 mm +0.010 / -0.000, true position 0.025 mm."),
         ("4. Finish", "Shot peen per KPS-2200, intensity 0.008-0.012A.")],
        marking=marking)

    misplaced = ROOT / "Shared/Misc/bracket_reference_copy.pdf"
    misplaced.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(itar, misplaced)

    # --- Supplier datasheet: Procurement only ---
    pdf(ROOT / "Procurement/Suppliers/SUP-0031_Apex_Bar_Stock_Datasheet.pdf",
        "Apex Metals Bar Stock Datasheet",
        ["Supplier SUP-0031", "Pricing tier: confidential", "Released"],
        [("Products", "17-4 PH round bar, 0.5 to 6 inch diameter, Condition A."),
         ("Lead time", "Standard 6 weeks; expedite 2 weeks at 15 percent premium."),
         ("Certifications", "AS9100D, mill certs supplied with every heat.")])

    files = sorted(p for p in ROOT.rglob("*") if p.is_file())
    print(f"Created {len(files)} files under {ROOT}/")
    for f in files:
        print("  ", f.relative_to(ROOT))


if __name__ == "__main__":
    main()
