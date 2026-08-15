"""
Tests for PDF Auto-Tagging & Accessibility Remediation Engine
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.engine import AutoTaggingEngine, AccessibilityValidator, StandardTag
import pikepdf
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from PIL import Image as PILImage, ImageDraw


def generate_test_pdf_file(output_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img_path = os.path.join(os.path.dirname(output_path), "sample_chart.png")
    img = PILImage.new("RGB", (400, 200), color=(240, 245, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 380, 180], outline=(40, 80, 160), width=2)
    draw.rectangle([50, 100, 100, 170], fill=(50, 120, 220))
    draw.rectangle([130, 70, 180, 170], fill=(40, 180, 140))
    img.save(img_path)

    doc = SimpleDocTemplate(output_path, pagesize=letter, rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=22, leading=26, textColor=colors.HexColor("#1A202C"))
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=16, leading=20, textColor=colors.HexColor("#2D3748"))
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=14, textColor=colors.HexColor("#2D3748"))

    story = [
        Paragraph("Annual Digital Accessibility Report 2026", h1),
        Paragraph("Executive Summary", h2),
        Paragraph("This document is remediated to achieve 100% compliance with PDF/UA-1 (ISO 14289) and WCAG 2.1 AA standards.", body),
        Paragraph("• Multi-line paragraph unification.", body),
        Paragraph("• Explicit table header scopes.", body),
        Spacer(1, 10),
        Table([["Quarter", "Remediated Docs", "Score"], ["Q1 2026", "14,250", "99.8%"]], colWidths=[120, 140, 120]),
        Spacer(1, 10),
        RLImage(img_path, width=320, height=140)
    ]
    doc.build(story)


def test_end_to_end_autotagging():
    test_dir = os.path.join(os.path.dirname(__file__), "..", "test_assets")
    os.makedirs(test_dir, exist_ok=True)
    input_pdf = os.path.join(test_dir, "sample_document.pdf")
    output_pdf = os.path.join(test_dir, "sample_document_tagged.pdf")

    if not os.path.exists(input_pdf):
        generate_test_pdf_file(input_pdf)

    assert os.path.exists(input_pdf), "Test asset must exist"

    engine = AutoTaggingEngine(ocr_enabled=True)
    result = engine.process_pdf(
        input_pdf_path=input_pdf,
        output_pdf_path=output_pdf,
        custom_metadata={
            "title": "Annual Accessibility & Compliance Audit 2026",
            "author": "Antigravity AI",
            "language": "en-US"
        }
    )

    assert result.success is True
    assert os.path.exists(output_pdf)
    assert result.audit_report is not None
    assert result.audit_report.accessibility_score >= 85.0

    pdf = pikepdf.open(output_pdf)
    assert "/MarkInfo" in pdf.Root
    assert bool(pdf.Root.MarkInfo.Marked) is True
    assert "/ViewerPreferences" in pdf.Root
    assert bool(pdf.Root.ViewerPreferences.DisplayDocTitle) is True
    assert str(pdf.Root.get("/Lang", "")) == "en-US"
    assert "/StructTreeRoot" in pdf.Root
    assert "/RoleMap" in pdf.Root.StructTreeRoot
    assert "/ParentTree" in pdf.Root.StructTreeRoot

    for page in pdf.pages:
        assert str(page.get("/Tabs", "")) == "/S"
        stream_ops = list(pikepdf.parse_content_stream(page))
        bdc_ops = [op for op in stream_ops if op.operator == pikepdf.Operator("BDC")]
        assert len(bdc_ops) > 0, "Page content stream must contain BDC marked content sequences"

    # ---- Core tagging guarantees (regression coverage) --------------------------------

    # 1. ParentTree must be populated (not the empty /Nums that the old engine emitted).
    parent_tree = pdf.Root.StructTreeRoot.ParentTree
    assert "/Nums" in parent_tree
    nums = parent_tree["/Nums"]
    assert len(nums) >= 2, "ParentTree /Nums must contain at least one page entry"
    assert len(nums) % 2 == 0, "ParentTree /Nums must be [key, value] pairs"

    # 2. Every page /StructParents must resolve to an entry in the ParentTree.
    tree_keys = {int(nums[i]) for i in range(0, len(nums), 2)}
    for page in pdf.pages:
        sp = page.get("/StructParents")
        assert sp is not None, "Every page must carry /StructParents"
        assert int(sp) in tree_keys, "Page /StructParents must resolve in the ParentTree"

    # 3. MCIDs referenced by the struct tree must be globally unique (PDF/UA-1 7.4.1).
    mcid_counts = {}

    def collect_mcids(node):
        if isinstance(node, pikepdf.Array):
            for item in node:
                collect_mcids(item)
            return
        if not isinstance(node, pikepdf.Dictionary):
            return
        if node.get("/Type") == pikepdf.Name("/MCR"):
            mcid = int(node.get("/MCID"))
            mcid_counts[mcid] = mcid_counts.get(mcid, 0) + 1
        kids = node.get("/K")
        if kids is not None:
            collect_mcids(kids)

    collect_mcids(pdf.Root.StructTreeRoot.K)
    assert len(mcid_counts) > 0, "Structure tree must reference marked content"
    duplicates = [m for m, c in mcid_counts.items() if c > 1]
    assert not duplicates, f"MCIDs must be globally unique, duplicates found: {duplicates[:5]}"

    # 4. No empty StructElems: every leaf element must own marked content.
    empty_elems = []

    def check_no_empty(node):
        if isinstance(node, pikepdf.Array):
            for item in node:
                check_no_empty(item)
            return
        if not isinstance(node, pikepdf.Dictionary):
            return
        if node.get("/Type") == pikepdf.Name("/StructElem"):
            kids = node.get("/K")
            kid_count = len(kids) if isinstance(kids, pikepdf.Array) else (1 if kids is not None else 0)
            if kid_count == 0:
                empty_elems.append(str(node.get("/S")))
        kids = node.get("/K")
        if kids is not None:
            check_no_empty(kids)

    check_no_empty(pdf.Root.StructTreeRoot.K)
    assert not empty_elems, f"Empty StructElems found: {empty_elems[:5]}"

    # 5. BDC/BMC/EMC must be balanced in every page content stream.
    for page in pdf.pages:
        stream_ops = list(pikepdf.parse_content_stream(page))
        depth = 0
        for op in stream_ops:
            if op.operator in (pikepdf.Operator("BDC"), pikepdf.Operator("BMC")):
                depth += 1
            elif op.operator == pikepdf.Operator("EMC"):
                depth -= 1
        assert depth == 0, "BDC/BMC/EMC operators must be balanced in every page stream"

    pdf.close()
    print("\n[SUCCESS] ALL ACCESSIBILITY ENGINE ASSERTIONS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    import traceback
    try:
        test_end_to_end_autotagging()
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
