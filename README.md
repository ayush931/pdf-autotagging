# PDF Auto-Tagging & Accessibility Remediation Engine

An enterprise-grade, high-performance automated PDF remediation and tagging engine integrated with **[OpenDataLoader PDF](https://github.com/opendataloader-project/opendataloader-pdf.git)** (XY-Cut++ reading order, cluster table recognition, and Document Layout Analysis) combined with low-level **PDF/UA-1 & WCAG 2.1/2.2 AA** structure tree and marked content stream injection.

---

## Standards Compliance

- **PDF/UA-1 (ISO 14289-1:2014)** & **PDF/UA-2 (ISO 14289-2:2024)**
- **WCAG 2.1 & 2.2 Level AA / AAA**
- **Section 508 (US Rehabilitation Act)**
- **EN 301 549 (European Accessibility Standard)**
- **HHS (US Dept of Health & Human Services) Accessibility Guidelines**
- **Matterhorn Protocol 1.1 Conformance**

---

## Key Capabilities

1. **OpenDataLoader PDF Engine Integration**:
   - Advanced **XY-Cut++ reading order** algorithm for complex multi-column documents.
   - **Cluster + Border table detection** with cell spans and header scope determination.
   - High-accuracy semantic layout classification (Headings, Paragraphs, Lists, Figures, Tables, Captions).

2. **Automatic Document Normalization & Repair**:
   - Detects and fixes skewed, rotated, or distorted pages using computer vision deskewing.
   - Detects scanned/raster-only documents and reconstructs a high-precision searchable/accessible OCR text layer.
   - Normalizes font encodings, corrects missing `ToUnicode` CMap tables, and sanitizes PDF stream syntax.

3. **Unified Paragraph & Block Tagging**:
   - Multi-line paragraphs are structured as **a single `<P>` tag** containing all text lines, preventing fragmented line-by-line reading.
   - Multi-line headings structured as **single `<H1>`–`<H6>` tags**.
   - Contiguous lists structured as `<L>` &rarr; `<LI>` &rarr; `<Lbl>` + `<LBody>`.

4. **Low-Level PDF Structure Tree & Content Stream Injection**:
   - Rewrites page content streams (`/Contents`) with exact `/<Tag> <</MCID n>> BDC ... EMC` marked content sequences.
   - Populates `/StructTreeRoot`, `/RoleMap`, `/ParentTree` (NumberTree), `/Tabs /S` (structure-order tab traversal), and `/MCR` dictionaries.
   - Injects Dublin Core XMP metadata with `pdfuaid:part 1` conformance schemas.
   - Generates contextual alternative text (`/Alt`) for all illustrations and charts.

5. **Built-in Compliance Auditor & Validator**:
   - Automated 35+ rule validation engine matching PAC (PDF Accessibility Checker) and Adobe Preflight.
   - Generates an accessibility compliance score (0–100%) and actionable JSON issue reports.

---

## Directory Structure

```
pdf-autotagging/
├── src/
│   ├── engine/
│   │   ├── __init__.py                # Package exports & public API
│   │   ├── models.py                  # Pydantic schemas (Tags, BBoxes, Table Models, Audit Reports)
│   │   ├── core.py                    # AutoTaggingEngine master pipeline coordinator
│   │   ├── opendataloader_adapter.py  # OpenDataLoader PDF integration adapter
│   │   ├── normalizer.py              # Skew/rotation repair, OCR layer injection, CMap sanitization
│   │   ├── layout_detector.py         # Native typographic layout classifier (<H1>-<H6>, <P>, <L>, etc.)
│   │   ├── table_extractor.py         # High-speed table grid parser (TH/TD, Column/Row Scopes)
│   │   ├── reading_order.py           # Multi-column topological flow sorter
│   │   ├── tagger.py                  # Low-level StructTreeRoot, ParentTree, & MCID stream injector
│   │   ├── pdf_ua.py                  # PDF/UA-1 XMP Dublin Core metadata builder
│   │   ├── contrast_checker.py        # WCAG 1.4.3 color contrast validator
│   │   ├── alt_text_gen.py            # Contextual figure and chart Alt-text generator
│   │   └── validator.py               # PDF/UA & WCAG Matterhorn compliance auditor
│   └── cli.py                         # Enterprise command-line interface
├── test_assets/                       # Test generators & sample documents
├── tests/
│   └── test_engine.py                 # Automated test suite
├── requirements.txt
├── setup.py
└── README.md
```

---

## Quick Start

### 1. Installation

```bash
pip install -r requirements.txt
```

### 2. Command Line Interface (CLI)

```bash
# Auto-tag with OpenDataLoader layout & XY-Cut++ reading order
python src/cli.py input.pdf -o output_tagged.pdf -v --report audit_report.json

# Customize table detection and reading order methods
python src/cli.py input.pdf -o output_tagged.pdf --table-method cluster --reading-order xycut

# Batch directory processing
python src/cli.py ./input_folder/ -o ./output_folder/ -v
```

### 3. Python SDK Usage

```python
from src.engine import AutoTaggingEngine
from src.engine.logger import Verbosity

engine = AutoTaggingEngine(
    ocr_enabled=True,
    use_opendataloader=True,
    verbosity=Verbosity.VERBOSE
)

result = engine.process_pdf(
    input_pdf_path="9781771123341_Web.pdf",
    output_pdf_path="output_tagged.pdf",
    custom_metadata={"title": "Homeless Youth and the Search for Stability", "language": "en-US"},
    table_method="cluster",
    reading_order="xycut"
)

print(f"Accessibility Score: {result.audit_report.accessibility_score}%")
print(f"PDF/UA Compliant: {result.audit_report.is_pdf_ua_compliant}")
print(f"Total MCIDs Injected: {result.total_marked_content_sequences}")
```

### 4. Run Automated Test Suite

```bash
python tests/test_engine.py
```
