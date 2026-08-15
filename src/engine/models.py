"""
PDF Auto-Tagging & Accessibility Data Models
Comprehensive schema for semantic elements, typography, table geometry, reading flow, and compliance auditing.
"""

from enum import Enum
from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field


class StandardTag(str, Enum):
    # Structural hierarchy
    DOCUMENT = "Document"
    PART = "Part"
    ART = "Art"
    SECT = "Sect"
    DIV = "Div"
    BLOCK_QUOTE = "BlockQuote"
    CAPTION = "Caption"
    TOC = "TOC"
    TOCI = "TOCI"
    INDEX = "Index"
    
    # Headings
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"
    H4 = "H4"
    H5 = "H5"
    H6 = "H6"
    
    # Paragraphs & Inline
    P = "P"
    SPAN = "Span"
    QUOTE = "Quote"
    NOTE = "Note"
    REFERENCE = "Reference"
    BIB_ENTRY = "BibEntry"
    CODE = "Code"
    
    # Lists
    L = "L"
    LI = "LI"
    LBL = "Lbl"
    LBODY = "LBody"
    
    # Tables
    TABLE = "Table"
    TR = "TR"
    TH = "TH"
    TD = "TD"
    THEAD = "THead"
    TBODY = "TBody"
    TFOOT = "TFoot"
    
    # Illustrations & Figures
    FIGURE = "Figure"
    FORMULA = "Formula"
    FORM = "Form"
    LINK = "Link"
    
    # Artifacts (Ignored by screen readers)
    ARTIFACT = "Artifact"
    NON_STRUCT = "NonStruct"


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def intersects(self, other: "BoundingBox") -> bool:
        return not (self.x1 < other.x0 or self.x0 > other.x1 or self.y1 < other.y0 or self.y0 > other.y1)

    def union(self, other: "BoundingBox") -> "BoundingBox":
        return BoundingBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )


class TableCellModel(BaseModel):
    row_index: int
    col_index: int
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    header_scope: Optional[str] = None  # "Column", "Row", "Both"
    text: str = ""
    bbox: BoundingBox
    mcid: Optional[int] = None
    headers: List[str] = Field(default_factory=list)


class TableModel(BaseModel):
    bbox: BoundingBox
    rows_count: int
    cols_count: int
    cells: List[TableCellModel] = Field(default_factory=list)
    has_headers: bool = False
    caption: Optional[str] = None
    summary: Optional[str] = None


class SemanticElement(BaseModel):
    id: str
    tag: StandardTag
    page_num: int  # 0-indexed
    reading_order_index: int = 0
    bbox: BoundingBox
    text: str = ""
    alt_text: Optional[str] = None
    actual_text: Optional[str] = None
    expansion_text: Optional[str] = None
    lang: Optional[str] = None
    
    # Typographic & Styling attributes
    font_name: Optional[str] = None
    font_size: Optional[float] = None
    font_weight: Optional[str] = None
    font_color: Optional[str] = None
    bg_color: Optional[str] = None
    contrast_ratio: Optional[float] = None
    
    # Hierarchy
    parent_id: Optional[str] = None
    children_ids: List[str] = Field(default_factory=list)
    
    # Low-level PDF Tagging info
    mcid: Optional[int] = None
    is_artifact: bool = False
    artifact_type: Optional[str] = None  # "Pagination", "Header", "Footer", "Decorative"
    
    # Specialized structures
    table_data: Optional[TableModel] = None
    list_level: int = 0
    list_label: Optional[str] = None
    link_uri: Optional[str] = None
    formula_latex: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)


class PageLayoutModel(BaseModel):
    page_num: int
    width: float
    height: float
    rotation: int = 0
    elements: List[SemanticElement] = Field(default_factory=list)
    reading_order: List[str] = Field(default_factory=list)
    num_columns: int = 1
    column_boundaries: List[float] = Field(default_factory=list)
    has_images: bool = False
    is_scanned: bool = False
    total_mcids: int = 0


class DocumentMetadata(BaseModel):
    title: str = ""
    author: Optional[str] = ""
    subject: Optional[str] = ""
    keywords: List[str] = Field(default_factory=list)
    language: str = "en-US"
    creator: str = "Antigravity PDF Accessibility Engine"
    producer: str = "Antigravity PDF/UA AutoTagger"
    is_tagged: bool = False
    pdf_version: str = "1.7"
    pdf_ua_version: str = "PDF/UA-1:2014"


class AuditSeverity(str, Enum):
    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"
    INFO = "Info"


class AuditIssue(BaseModel):
    rule_id: str
    standard: str  # "PDF/UA-1", "PDF/UA-2", "WCAG 2.1 AA", "WCAG 2.2 AA", "Section 508"
    clause: str
    title: str
    description: str
    severity: AuditSeverity
    page_num: Optional[int] = None
    element_id: Optional[str] = None
    element_tag: Optional[str] = None
    fix_applied: Optional[str] = None
    status: str = "FAIL"  # "FAIL", "PASS", "REMEDIATED"


class AccessibilityAuditReport(BaseModel):
    document_title: str
    total_pages: int
    is_pdf_ua_compliant: bool
    is_wcag_aa_compliant: bool
    accessibility_score: float  # 0 to 100
    total_issues_found: int
    total_issues_fixed: int
    issues: List[AuditIssue] = Field(default_factory=list)
    tag_counts: Dict[str, int] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)


class AutoTaggingResult(BaseModel):
    success: bool
    input_pdf_path: str
    output_pdf_path: str
    audit_report: AccessibilityAuditReport
    metadata: DocumentMetadata
    pages: List[PageLayoutModel] = Field(default_factory=list)
    processing_time_sec: float
    total_tags_created: int = 0
    total_marked_content_sequences: int = 0
    message: str
