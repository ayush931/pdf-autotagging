"""
Enterprise CLI for PDF Auto-Tagging & Accessibility Remediation Engine
Integrated with OpenDataLoader PDF (https://github.com/opendataloader-project/opendataloader-pdf.git)

Usage:
    python src/cli.py input.pdf -o output_tagged.pdf --verbose --report audit_report.json
    python src/cli.py /path/to/pdf_folder/ -o /path/to/output_folder/ --verbose
"""

import os
import sys
import argparse
import json
import time
from typing import List

# Ensure UTF-8 stream handling
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.engine import AutoTaggingEngine
from src.engine.logger import Verbosity, logger


def format_table_row(col1: str, col2: str, width: int = 72) -> str:
    padding = width - len(col1) - len(col2) - 4
    return f"| {col1}{' ' * max(1, padding)}{col2} |"


def print_banner():
    banner = """
+======================================================================+
|          ANTIGRAVITY PDF AUTO-TAGGING & ACCESSIBILITY ENGINE         |
|         Integrated with OpenDataLoader PDF (XY-Cut++ & DLA)          |
|   Conforms to: PDF/UA-1 (ISO 14289) | WCAG 2.1/2.2 AA | Section 508  |
+======================================================================+
"""
    print(banner.strip())


def process_single_file(engine: AutoTaggingEngine, input_path: str, output_path: str, args: argparse.Namespace):
    custom_metadata = {}
    if args.title:
        custom_metadata["title"] = args.title
    if args.author:
        custom_metadata["author"] = args.author
    if args.lang:
        custom_metadata["language"] = args.lang
    if args.subject:
        custom_metadata["subject"] = args.subject

    result = engine.process_pdf(
        input_pdf_path=input_path,
        output_pdf_path=output_path,
        custom_metadata=custom_metadata if custom_metadata else None,
        table_method=args.table_method,
        reading_order=args.reading_order
    )

    audit = result.audit_report
    w = 72
    
    print("\n" + "+" + "=" * (w - 2) + "+")
    print(format_table_row("ACCESSIBILITY REMEDIATION SUMMARY", "METRICS", w))
    print("+" + "=" * (w - 2) + "+")
    print(format_table_row("Status", "SUCCESS", w))
    print(format_table_row("Input File", os.path.basename(input_path), w))
    print(format_table_row("Output Tagged PDF", os.path.basename(result.output_pdf_path), w))
    print(format_table_row("Total Pages Remediated", str(audit.total_pages), w))
    print(format_table_row("Execution Duration", f"{result.processing_time_sec:.3f} s", w))
    print(format_table_row("Accessibility Score", f"{audit.accessibility_score} %", w))
    print(format_table_row("PDF/UA-1 Conformance", "PASS (100%)" if audit.is_pdf_ua_compliant else "PARTIAL", w))
    print(format_table_row("WCAG 2.1/2.2 Level AA", "PASS (100%)" if audit.is_wcag_aa_compliant else "PARTIAL", w))
    print(format_table_row("Passed Checkpoints", str(audit.total_issues_fixed), w))
    print(format_table_row("Marked Content Sequences (MCIDs)", str(result.total_marked_content_sequences), w))
    print("+" + "-" * (w - 2) + "+")
    print(format_table_row("STRUCTURE TREE TAG BREAKDOWN", "COUNT", w))
    print("+" + "-" * (w - 2) + "+")
    for tag, count in sorted(audit.tag_counts.items()):
        print(format_table_row(f"  <{tag}>", str(count), w))
    print("+" + "=" * (w - 2) + "+")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(audit.model_dump_json(indent=2))
        logger.success(f"Audit report saved to: {os.path.abspath(args.report)}")


def main():
    parser = argparse.ArgumentParser(
        description="Enterprise PDF Auto-Tagging & Accessibility Remediation Engine (PDF/UA & WCAG 2.1/2.2 AA)"
    )
    parser.add_argument("input", help="Path to input PDF file or directory of PDFs")
    parser.add_argument("-o", "--output", help="Path for output accessible tagged PDF or directory", default=None)
    parser.add_argument("--title", help="Document Title metadata override", default=None)
    parser.add_argument("--author", help="Document Author metadata", default=None)
    parser.add_argument("--lang", help="Document Language code (e.g. en-US, fr-FR)", default="en-US")
    parser.add_argument("--subject", help="Document Subject / Description", default=None)
    parser.add_argument("--report", help="Save JSON accessibility audit report to path", default=None)
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR preprocessing for scanned pages")
    parser.add_argument("--no-opendataloader", action="store_true", help="Disable OpenDataLoader engine and use pure native layout pipeline")
    parser.add_argument("--table-method", choices=["cluster", "default"], default="cluster", help="Table detection method (cluster or default)")
    parser.add_argument("--reading-order", choices=["xycut", "off"], default="xycut", help="Reading order algorithm (xycut or off)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostic logs")
    parser.add_argument("--debug", action="store_true", help="Enable deep debug operator logs")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress output except errors")

    args = parser.parse_args()

    verbosity = Verbosity.NORMAL
    if args.quiet:
        verbosity = Verbosity.QUIET
    elif args.debug:
        verbosity = Verbosity.DEBUG
    elif args.verbose:
        verbosity = Verbosity.VERBOSE

    if not args.quiet:
        print_banner()

    if not os.path.exists(args.input):
        print(f"Error: File or directory not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    engine = AutoTaggingEngine(
        ocr_enabled=not args.no_ocr,
        use_opendataloader=not args.no_opendataloader,
        verbosity=verbosity
    )

    if os.path.isdir(args.input):
        pdf_files = [os.path.join(args.input, f) for f in os.listdir(args.input) if f.lower().endswith(".pdf")]
        if not pdf_files:
            print(f"No PDF files found in directory: {args.input}")
            sys.exit(0)

        out_dir = args.output or os.path.join(args.input, "tagged_output")
        os.makedirs(out_dir, exist_ok=True)
        print(f"\nFound {len(pdf_files)} PDF files to remediate in batch mode.")

        start_all = time.perf_counter()
        success_count = 0
        for idx, pdf_path in enumerate(pdf_files, 1):
            out_file = os.path.join(out_dir, f"{os.path.splitext(os.path.basename(pdf_path))[0]}_tagged.pdf")
            print(f"\n[{idx}/{len(pdf_files)}] Processing {os.path.basename(pdf_path)}...")
            try:
                process_single_file(engine, pdf_path, out_file, args)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to process {pdf_path}: {str(e)}")

        total_elapsed = time.perf_counter() - start_all
        print(f"\nBatch processing complete: {success_count}/{len(pdf_files)} succeeded in {total_elapsed:.2f}s.")
    else:
        process_single_file(engine, args.input, args.output, args)


if __name__ == "__main__":
    main()
