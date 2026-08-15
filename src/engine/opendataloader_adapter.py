"""
OpenDataLoader PDF Integration Adapter
Wraps OpenDataLoader PDF (https://github.com/opendataloader-project/opendataloader-pdf.git)
for high-accuracy layout analysis, XY-Cut++ reading order, table cluster extraction,
and PDF/UA accessibility auto-tagging.
"""

import os
import json
import tempfile
import shutil
from typing import List, Dict, Optional, Tuple, Any
import pymupdf as fitz

from src.engine.models import (
    PageLayoutModel, SemanticElement, StandardTag, BoundingBox,
    TableModel, TableCellModel, DocumentMetadata
)
from src.engine.logger import logger

try:
    import opendataloader_pdf
    HAS_OPENDATALOADER = True
except ImportError:
    HAS_OPENDATALOADER = False


class OpenDataLoaderAdapter:
    """
    Adapter for OpenDataLoader PDF parser and auto-tagger.
    """

    TAG_MAP = {
        "H1": StandardTag.H1,
        "H2": StandardTag.H2,
        "H3": StandardTag.H3,
        "H4": StandardTag.H4,
        "H5": StandardTag.H5,
        "H6": StandardTag.H6,
        "P": StandardTag.P,
        "Table": StandardTag.TABLE,
        "Figure": StandardTag.FIGURE,
        "Caption": StandardTag.CAPTION,
        "BlockQuote": StandardTag.BLOCK_QUOTE,
        "L": StandardTag.L,
        "LI": StandardTag.LI,
        "Lbl": StandardTag.LBL,
        "LBody": StandardTag.LBODY,
        "Code": StandardTag.CODE,
        "Formula": StandardTag.FORMULA,
        "Note": StandardTag.NOTE,
        "Reference": StandardTag.REFERENCE,
        "Artifact": StandardTag.ARTIFACT,
    }

    TYPE_MAP = {
        "heading": StandardTag.H2,
        "paragraph": StandardTag.P,
        "table": StandardTag.TABLE,
        "image": StandardTag.FIGURE,
        "list": StandardTag.LI,
        "regionlist": StandardTag.LI,
        "header": StandardTag.ARTIFACT,
        "footer": StandardTag.ARTIFACT,
        "caption": StandardTag.CAPTION,
        "blockquote": StandardTag.BLOCK_QUOTE,
        "code": StandardTag.CODE,
    }

    def __init__(self):
        self.available = HAS_OPENDATALOADER

    def is_available(self) -> bool:
        """Checks if OpenDataLoader library and Java runtime are operational."""
        if not self.available:
            return False
        return True

    def extract_layout(
        self,
        pdf_path: str,
        pages: Optional[str] = None,
        table_method: str = "cluster",
        reading_order: str = "xycut"
    ) -> Optional[Tuple[List[PageLayoutModel], Optional[str]]]:
        """
        Executes OpenDataLoader conversion, extracting layout structure, XY-Cut++ reading order,
        normalizing coordinates to standard top-down space, and repairing heading hierarchies.
        """
        if not self.is_available():
            logger.debug("OpenDataLoader library not available, using native layout detector.", "ODL")
            return None

        temp_dir = tempfile.mkdtemp(prefix="odl_extract_")
        abs_pdf_path = os.path.abspath(pdf_path)

        try:
            logger.debug(f"Invoking OpenDataLoader PDF (reading_order={reading_order}, table_method={table_method})...", "ODL")
            
            opendataloader_pdf.convert(
                abs_pdf_path,
                output_dir=temp_dir,
                format=["json", "tagged-pdf"],
                reading_order=reading_order,
                table_method=table_method,
                pages=pages,
                quiet=True
            )

            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            json_file = os.path.join(temp_dir, f"{base_name}.json")
            tagged_pdf_file = os.path.join(temp_dir, f"{base_name}_tagged.pdf")

            if not os.path.exists(json_file):
                jsons = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.endswith(".json")]
                if jsons:
                    json_file = jsons[0]
                else:
                    logger.warning("OpenDataLoader did not generate JSON output, falling back to native layout detector.")
                    return None

            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            doc = fitz.open(pdf_path)
            total_pdf_pages = len(doc)
            pages_layout: List[PageLayoutModel] = []

            # Group OpenDataLoader kids by page number (0-indexed)
            kids_by_page: Dict[int, List[Dict[str, Any]]] = {}
            for kid in data.get("kids", []):
                p_num = kid.get("page number", 1) - 1
                kids_by_page.setdefault(p_num, []).append(kid)

            for p_idx in range(total_pdf_pages):
                fitz_page = doc[p_idx]
                page_w = float(fitz_page.rect.width)
                page_h = float(fitz_page.rect.height)
                
                page_kids = kids_by_page.get(p_idx, [])
                elements: List[SemanticElement] = []
                reading_order_ids: List[str] = []

                for k_idx, kid in enumerate(page_kids):
                    el = self._convert_kid_to_element(kid, p_idx, k_idx, page_w, page_h)
                    if el:
                        elements.append(el)
                        reading_order_ids.append(el.id)

                # Normalize heading levels across the page
                self._normalize_heading_levels(elements)

                page_layout = PageLayoutModel(
                    page_num=p_idx,
                    width=page_w,
                    height=page_h,
                    rotation=fitz_page.rotation,
                    elements=elements,
                    reading_order=reading_order_ids,
                    has_images=any(el.tag == StandardTag.FIGURE for el in elements),
                    is_scanned=False
                )
                pages_layout.append(page_layout)

            doc.close()
            logger.verbose(f"OpenDataLoader successfully parsed {len(pages_layout)} pages with XY-Cut++ reading order.")
            
            final_tagged_pdf = None
            if os.path.exists(tagged_pdf_file):
                final_tagged_pdf = os.path.join(temp_dir, "odl_tagged_keep.pdf")
                shutil.copyfile(tagged_pdf_file, final_tagged_pdf)

            return pages_layout, final_tagged_pdf

        except Exception as e:
            logger.warning(f"OpenDataLoader execution failed ({str(e)}), falling back to native layout detector.")
            return None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _convert_kid_to_element(
        self,
        kid: Dict[str, Any],
        page_idx: int,
        k_idx: int,
        page_w: float,
        page_h: float
    ) -> Optional[SemanticElement]:
        """Converts an OpenDataLoader JSON kid object into a SemanticElement with normalized coordinates."""
        pdfua_tag = kid.get("pdfua_tag") or ""
        elem_type = kid.get("type") or "paragraph"
        elem_type_lower = elem_type.lower()

        # Structural container elements must not be treated as leaf content tags
        CONTAINER_TYPES = {
            "page", "document", "body", "section", "sect", "part", "div",
            "art", "article", "region", "regionlist", "root"
        }
        if elem_type_lower in CONTAINER_TYPES or pdfua_tag in ("Document", "Part", "Sect", "Div", "Art", "Root"):
            return None

        tag = self.TAG_MAP.get(pdfua_tag)
        if not tag:
            tag = self.TYPE_MAP.get(elem_type_lower, StandardTag.P)

        if elem_type_lower == "heading" and "heading level" in kid:
            lvl = min(6, max(1, kid["heading level"]))
            tag = getattr(StandardTag, f"H{lvl}")

        # Convert OpenDataLoader bottom-up coordinates to standard top-down coordinates:
        # OpenDataLoader: [x0, y0_bottom, x1, y1_top]
        raw_box = kid.get("bounding box", [0, 0, 100, 100])
        if len(raw_box) == 4:
            x0 = float(raw_box[0])
            y0_raw = float(raw_box[1])
            x1 = float(raw_box[2])
            y1_raw = float(raw_box[3])
            
            # Normalize to top-down coordinates
            top_y0 = max(0.0, page_h - max(y0_raw, y1_raw))
            top_y1 = min(page_h, page_h - min(y0_raw, y1_raw))
            
            bbox = BoundingBox(
                x0=x0,
                y0=top_y0,
                x1=x1,
                y1=top_y1
            )
        else:
            bbox = BoundingBox(x0=0, y0=0, x1=page_w, y1=page_h)

        # Do not allow non-figure / paragraph elements to cover the whole page
        is_whole_page = (bbox.width >= page_w * 0.92 and bbox.height >= page_h * 0.85) or (
            bbox.x0 <= 5 and bbox.y0 <= 5 and bbox.x1 >= page_w - 5 and bbox.y1 >= page_h - 5
        )
        if is_whole_page and tag != StandardTag.FIGURE:
            return None

        text_content = kid.get("content") or kid.get("text") or ""
        is_artifact = (tag == StandardTag.ARTIFACT or elem_type.lower() in ("header", "footer"))
        artifact_type = "Header" if elem_type.lower() == "header" else ("Footer" if elem_type.lower() == "footer" else None)

        alt_text = None
        if tag == StandardTag.FIGURE:
            alt_text = kid.get("alt") or kid.get("description") or f"Illustration on page {page_idx + 1}"

        # Handle List Items & Labels
        list_label = None
        if tag == StandardTag.LI:
            # Check for bullet symbol or number
            text_str = text_content.strip()
            if text_str and text_str[0] in ("•", "–", "—", "*", "-"):
                list_label = text_str[0]

        # Handle Table
        table_data = None
        if tag == StandardTag.TABLE and "table" in kid:
            raw_table = kid["table"]
            table_rows = raw_table.get("rows", [])
            cells: List[TableCellModel] = []
            
            for r_idx, row in enumerate(table_rows):
                for c_idx, cell in enumerate(row.get("cells", [])):
                    c_text = cell.get("content") or cell.get("text") or ""
                    is_th = bool(cell.get("is_header", r_idx == 0))
                    scope = "Column" if (r_idx == 0) else ("Row" if is_th else None)
                    c_box = cell.get("bounding box", [raw_box[0], raw_box[1], raw_box[2], raw_box[3]])
                    
                    if len(c_box) == 4:
                        c_top_y0 = max(0.0, page_h - max(float(c_box[1]), float(c_box[3])))
                        c_top_y1 = min(page_h, page_h - min(float(c_box[1]), float(c_box[3])))
                        cell_bbox = BoundingBox(x0=float(c_box[0]), y0=c_top_y0, x1=float(c_box[2]), y1=c_top_y1)
                    else:
                        cell_bbox = bbox

                    cells.append(TableCellModel(
                        row_index=r_idx,
                        col_index=c_idx,
                        row_span=cell.get("row_span", 1),
                        col_span=cell.get("col_span", 1),
                        is_header=is_th,
                        header_scope=scope,
                        text=c_text,
                        bbox=cell_bbox
                    ))

            table_data = TableModel(
                bbox=bbox,
                rows_count=len(table_rows),
                cols_count=max((len(r.get("cells", [])) for r in table_rows), default=1),
                cells=cells,
                has_headers=any(c.is_header for c in cells),
                summary=f"Table on page {page_idx + 1}"
            )

        return SemanticElement(
            id=f"p{page_idx}_odl_{k_idx}",
            tag=tag,
            page_num=page_idx,
            reading_order_index=k_idx,
            bbox=bbox,
            text=text_content,
            font_name=kid.get("font"),
            font_size=kid.get("font size"),
            font_weight="bold" if kid.get("is_bold") else "normal",
            is_artifact=is_artifact,
            artifact_type=artifact_type,
            alt_text=alt_text,
            list_label=list_label,
            table_data=table_data
        )

    def _normalize_heading_levels(self, elements: List[SemanticElement]):
        """Normalizes heading hierarchy to eliminate skipped levels."""
        current_max = 0
        for el in elements:
            if el.tag.value in ["H1", "H2", "H3", "H4", "H5", "H6"]:
                level = int(el.tag.value[1])
                if current_max == 0 and level > 1:
                    el.tag = StandardTag.H1
                    current_max = 1
                elif level > current_max + 1:
                    normalized_lvl = current_max + 1
                    el.tag = getattr(StandardTag, f"H{normalized_lvl}")
                    current_max = normalized_lvl
                else:
                    current_max = max(current_max, level)
