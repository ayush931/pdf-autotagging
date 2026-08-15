"""
Master PDF Auto-Tagging & Accessibility Remediation Engine
Integrates OpenDataLoader PDF (XY-Cut++, Cluster Table Analysis, PDF/UA Tagging)
with native document repair, paragraph stream injection, and WCAG/PDF-UA auditing.
"""

import os
import time
import shutil
import tempfile
from typing import Optional, Dict, Any, List

from src.engine.models import (
    AutoTaggingResult, DocumentMetadata, AccessibilityAuditReport, PageLayoutModel
)
from src.engine.normalizer import PDFNormalizer
from src.engine.layout_detector import LayoutDetector
from src.engine.opendataloader_adapter import OpenDataLoaderAdapter
from src.engine.tagger import PDFTagger
from src.engine.validator import AccessibilityValidator
from src.engine.logger import logger, Verbosity


class AutoTaggingEngine:
    """
    Enterprise-grade PDF Auto-Tagging & Accessibility Remediation Engine.
    Combines OpenDataLoader PDF (XY-Cut++, table clustering) with low-level
    marked content stream rewriting and comprehensive PDF/UA-1 & WCAG 2.1/2.2 AA validation.
    """

    def __init__(
        self,
        ocr_enabled: bool = True,
        use_opendataloader: bool = True,
        verbosity: Verbosity = Verbosity.NORMAL
    ):
        self.ocr_enabled = ocr_enabled
        self.use_opendataloader = use_opendataloader
        logger.set_verbosity(verbosity)
        
        self.normalizer = PDFNormalizer(ocr_enabled=ocr_enabled)
        self.odl_adapter = OpenDataLoaderAdapter() if use_opendataloader else None
        self.layout_detector = LayoutDetector()
        self.tagger = PDFTagger()
        self.validator = AccessibilityValidator()

    def process_pdf(
        self,
        input_pdf_path: str,
        output_pdf_path: Optional[str] = None,
        custom_metadata: Optional[Dict[str, Any]] = None,
        table_method: str = "cluster",
        reading_order: str = "xycut"
    ) -> AutoTaggingResult:
        """
        Executes the full end-to-end auto-tagging and accessibility remediation workflow.
        """
        start_time = time.perf_counter()
        
        if not os.path.exists(input_pdf_path):
            logger.error(f"Input PDF file does not exist: {input_pdf_path}")
            raise FileNotFoundError(f"Input PDF not found: {input_pdf_path}")

        if not output_pdf_path:
            base_dir = os.path.dirname(input_pdf_path) or "."
            base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
            output_pdf_path = os.path.join(base_dir, f"{base_name}_accessible_tagged.pdf")

        logger.info(f"Target Input:  {os.path.abspath(input_pdf_path)}")
        logger.info(f"Target Output: {os.path.abspath(output_pdf_path)}")

        temp_dir = tempfile.mkdtemp(prefix="pdf_remediation_")
        repaired_pdf_path = os.path.join(temp_dir, "repaired.pdf")

        try:
            # Phase 1: Normalization & Document Repair
            logger.phase("Phase 1: Normalization, Deskewing & Syntax Repair")
            logger.start_timer("p1")
            _, metadata, page_stats = self.normalizer.normalize(
                input_pdf_path, repaired_pdf_path
            )
            p1_dur = logger.stop_timer("p1")
            logger.success(f"Phase 1 complete in {p1_dur:.3f}s ({len(page_stats)} pages sanitized)")

            # Apply custom metadata overrides if provided
            if custom_metadata:
                if "title" in custom_metadata and custom_metadata["title"]:
                    metadata.title = custom_metadata["title"]
                if "author" in custom_metadata:
                    metadata.author = custom_metadata["author"]
                if "language" in custom_metadata and custom_metadata["language"]:
                    metadata.language = custom_metadata["language"]
                if "subject" in custom_metadata:
                    metadata.subject = custom_metadata["subject"]

            # Phase 2: Layout Analysis & Semantic Structure Extraction (OpenDataLoader + Native)
            logger.phase("Phase 2: Deep Document Layout & XY-Cut++ Reading Flow")
            logger.start_timer("p2")
            pages_layout = None
            
            if self.odl_adapter and self.odl_adapter.is_available():
                logger.info("Engaging OpenDataLoader PDF engine (XY-Cut++ reading order & Cluster table recognition)...")
                odl_result = self.odl_adapter.extract_layout(
                    repaired_pdf_path,
                    table_method=table_method,
                    reading_order=reading_order
                )
                if odl_result:
                    pages_layout, _ = odl_result

            # Fallback to native layout detector if needed
            if not pages_layout:
                logger.info("Using native layout & typography detector...")
                pages_layout = self.layout_detector.analyze_document(
                    repaired_pdf_path, metadata
                )

            p2_dur = logger.stop_timer("p2")
            total_elements = sum(len(p.elements) for p in pages_layout)
            logger.success(f"Phase 2 complete in {p2_dur:.3f}s (Extracted {total_elements} semantic elements)")

            # Phase 3: Structure Tree & Low-Level PDF Tag Injection
            logger.phase("Phase 3: Marked Content Stream & Structure Tree Injection")
            logger.start_timer("p3")
            self.tagger.tag_document(
                repaired_pdf_path, output_pdf_path, pages_layout, metadata
            )
            p3_dur = logger.stop_timer("p3")
            total_mcids = sum(p.total_mcids for p in pages_layout)
            logger.success(f"Phase 3 complete in {p3_dur:.3f}s (Injected {total_mcids} marked content sequences)")

            # Phase 4: Compliance Auditing (PDF/UA-1 & WCAG 2.1/2.2 AA)
            logger.phase("Phase 4: PDF/UA & WCAG Compliance Validation")
            logger.start_timer("p4")
            audit_report = self.validator.audit_pdf(
                output_pdf_path, pages_layout, metadata
            )
            p4_dur = logger.stop_timer("p4")
            logger.success(f"Phase 4 complete in {p4_dur:.3f}s (Score: {audit_report.accessibility_score}%)")

            total_duration = round(time.perf_counter() - start_time, 3)

            return AutoTaggingResult(
                success=True,
                input_pdf_path=os.path.abspath(input_pdf_path),
                output_pdf_path=os.path.abspath(output_pdf_path),
                audit_report=audit_report,
                metadata=metadata,
                pages=pages_layout,
                processing_time_sec=total_duration,
                total_tags_created=total_elements,
                total_marked_content_sequences=total_mcids,
                message=f"PDF successfully remediated and tagged in {total_duration}s. Accessibility Score: {audit_report.accessibility_score}%"
            )

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
