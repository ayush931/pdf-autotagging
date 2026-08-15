"""
PDF Auto-Tagging & Accessibility Engine
"""

from src.engine.core import AutoTaggingEngine
from src.engine.models import (
    StandardTag, BoundingBox, SemanticElement, TableModel, TableCellModel,
    PageLayoutModel, DocumentMetadata, AccessibilityAuditReport,
    AutoTaggingResult, AuditIssue, AuditSeverity
)
from src.engine.opendataloader_adapter import OpenDataLoaderAdapter
from src.engine.validator import AccessibilityValidator
from src.engine.normalizer import PDFNormalizer
from src.engine.logger import Verbosity, logger

__all__ = [
    "AutoTaggingEngine",
    "OpenDataLoaderAdapter",
    "AccessibilityValidator",
    "PDFNormalizer",
    "Verbosity",
    "logger",
    "StandardTag",
    "BoundingBox",
    "SemanticElement",
    "TableModel",
    "TableCellModel",
    "PageLayoutModel",
    "DocumentMetadata",
    "AccessibilityAuditReport",
    "AutoTaggingResult",
    "AuditIssue",
    "AuditSeverity",
]
