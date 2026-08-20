"""
OpenDataLoader PDF Integration Adapter
Wraps OpenDataLoader PDF (https://github.com/opendataloader-project/opendataloader-pdf.git)
for high-accuracy layout analysis, XY-Cut++ reading order, table cluster extraction,
and PDF/UA accessibility auto-tagging.
"""

import os
import json
import re
import tempfile
import shutil
from typing import List, Dict, Optional, Tuple, Any
import pymupdf as fitz

from src.engine.models import (
    PageLayoutModel, SemanticElement, StandardTag, BoundingBox,
    TableModel, TableCellModel
)
from src.engine.reading_order import ReadingOrderEngine
from src.engine.table_extractor import TableExtractor
from src.engine.layout_detector import LayoutDetector
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
        "header": StandardTag.ARTIFACT,
        "footer": StandardTag.ARTIFACT,
        "caption": StandardTag.CAPTION,
        "blockquote": StandardTag.BLOCK_QUOTE,
        "code": StandardTag.CODE,
    }

    def __init__(self):
        self.available = HAS_OPENDATALOADER
        self.reading_order_engine = ReadingOrderEngine()
        self.table_extractor = TableExtractor()
        self.layout_detector = LayoutDetector()

    def is_available(self) -> bool:
        """Checks if the OpenDataLoader library is installed (Java availability is only verified at runtime)."""
        return self.available

    @staticmethod
    def _inside_bbox(inner: BoundingBox, outer: BoundingBox) -> bool:
        """Returns True when inner box is inside or substantially overlaps outer box."""
        ix0 = max(inner.x0, outer.x0)
        iy0 = max(inner.y0, outer.y0)
        ix1 = min(inner.x1, outer.x1)
        iy1 = min(inner.y1, outer.y1)
        if ix1 > ix0 and iy1 > iy0:
            inter_area = (ix1 - ix0) * (iy1 - iy0)
            inner_area = max(1.0, (inner.x1 - inner.x0) * (inner.y1 - inner.y0))
            if (inter_area / inner_area) > 0.35:
                return True
        return (
            inner.x0 >= outer.x0 - 5.0 and inner.x1 <= outer.x1 + 5.0 and
            inner.y0 >= outer.y0 - 5.0 and inner.y1 <= outer.y1 + 5.0
        )

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
                raw_page_text = fitz_page.get_text().strip()
                
                # Check for Table of Contents page
                is_toc_page = self.layout_detector._is_table_of_contents_page(raw_page_text, p_idx)
                
                elements: List[SemanticElement] = []
                reading_order_ids: List[str] = []

                if is_toc_page:
                    page_dict = fitz_page.get_text("dict")
                    elements = self.layout_detector._extract_toc_page_elements(
                        page_dict, p_idx, page_h, 10.5, 0
                    )
                else:
                    page_kids = kids_by_page.get(p_idx, [])
                    for k_idx, kid in enumerate(page_kids):
                        if str(kid.get("type", "")).lower() == "list":
                            for sub in self._convert_list_kid(kid, p_idx, k_idx, page_w, page_h):
                                if sub:
                                    elements.append(sub)
                        else:
                            el = self._convert_kid_to_element(kid, p_idx, k_idx, page_w, page_h)
                            if el:
                                elements.append(el)

                    # Complement with native TableExtractor if no table was found by ODL
                    if not any(el.tag == StandardTag.TABLE for el in elements):
                        try:
                            tables = self.table_extractor.extract_tables_from_page(pdf_path, p_idx, fitz_page)
                            if tables:
                                table_bboxes = [t.bbox for t in tables]
                                elements = [
                                    el for el in elements
                                    if el.tag == StandardTag.FIGURE or not any(self._inside_bbox(el.bbox, tb) for tb in table_bboxes)
                                ]
                                for t_idx, tbl in enumerate(tables):
                                    table_el = SemanticElement(
                                        id=f"p{p_idx}_tbl_{len(elements)}",
                                        tag=StandardTag.TABLE,
                                        page_num=p_idx,
                                        reading_order_index=len(elements),
                                        bbox=tbl.bbox,
                                        text="",
                                        table_data=tbl
                                    )
                                    elements.append(table_el)
                        except Exception:
                            pass

                # Complement with native raster images and vector drawing figures
                try:
                    page_dict = fitz_page.get_text("dict")
                    text_blocks = [b for b in page_dict.get("blocks", []) if b.get("type") == 0]
                    nat_figs, nat_arts = self.layout_detector._extract_page_graphics(
                        fitz_page, p_idx, page_w, page_h, text_blocks
                    )
                    existing_fig_boxes = [el.bbox for el in elements if el.tag == StandardTag.FIGURE]
                    for fig in nat_figs:
                        if not any(self._inside_bbox(fig.bbox, eb) for eb in existing_fig_boxes):
                            elements.append(fig)
                    for art in nat_arts:
                        elements.append(art)
                except Exception:
                    pass

                # Ensure no non-table text elements overlap table bounding boxes
                tbl_boxes = [el.bbox for el in elements if el.tag == StandardTag.TABLE]
                if tbl_boxes:
                    elements = [
                        el for el in elements
                        if el.tag in (StandardTag.TABLE, StandardTag.FIGURE)
                        or not any(self._inside_bbox(el.bbox, tb) for tb in tbl_boxes)
                    ]

                # Enrich generic figure alt text with the contextual generator
                # (ODL emits no alt text, so validators must see real content).
                self._enrich_figure_alt_text(elements, p_idx)

                # Order elements according to strict accessible reading order
                elements = self.reading_order_engine.order_page_elements(elements, page_w, page_h)
                reading_order_ids = [el.id for el in elements]

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

            # Document-wide heading hierarchy normalization
            self.layout_detector._normalize_heading_hierarchy(pages_layout)

            doc.close()
            logger.verbose(f"OpenDataLoader successfully parsed {len(pages_layout)} pages with XY-Cut++ reading order.")

            # The second tuple element used to return an ODL-tagged PDF, but the
            # file was deleted in the finally block before callers could use it.
            # The engine tags natively, so no tagged-PDF path is returned.
            return pages_layout, None

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

        # Do not allow any element (including figures or paragraphs) to cover the whole page
        is_whole_page = (bbox.width >= page_w * 0.92 and bbox.height >= page_h * 0.85) or (
            bbox.x0 <= 5 and bbox.y0 <= 5 and bbox.x1 >= page_w - 5 and bbox.y1 >= page_h - 5
        )
        if is_whole_page:
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

        # Handle Table. ODL emits table rows at the kid's top level ('rows'),
        # not under a nested 'table' key; cells carry 'row span'/'column span'
        # (with spaces), a 'pdfua_tag' of TH/TD, and their text lives in the
        # nested kids[].content.
        table_data = None
        if tag == StandardTag.TABLE:
            raw_table = kid.get("table") or kid
            table_rows = raw_table.get("rows", []) or []
            cells: List[TableCellModel] = []

            for r_idx, row in enumerate(table_rows):
                for c_idx, cell in enumerate(row.get("cells", []) or []):
                    cell_tag = str(cell.get("pdfua_tag", ""))
                    c_text = ""
                    for sub in cell.get("kids", []) or []:
                        c_text += str(sub.get("content") or sub.get("text") or "")
                    c_text = c_text.strip()
                    is_th = bool(cell.get("is_header", cell_tag == "TH" or r_idx == 0))
                    scope = "Column" if (r_idx == 0 or cell_tag == "TH") else ("Row" if is_th else None)
                    c_box = cell.get("bounding box", [raw_box[0], raw_box[1], raw_box[2], raw_box[3]])

                    if len(c_box) == 4:
                        c_top_y0 = max(0.0, page_h - max(float(c_box[1]), float(c_box[3])))
                        c_top_y1 = min(page_h, page_h - min(float(c_box[1]), float(c_box[3])))
                        cell_bbox = BoundingBox(x0=float(c_box[0]), y0=c_top_y0, x1=float(c_box[2]), y1=c_top_y1)
                    else:
                        cell_bbox = bbox

                    try:
                        row_span = int(cell.get("row span", cell.get("row_span", 1)))
                        col_span = int(cell.get("column span", cell.get("col_span", 1)))
                    except (TypeError, ValueError):
                        row_span = col_span = 1

                    cells.append(TableCellModel(
                        row_index=r_idx,
                        col_index=c_idx,
                        row_span=max(1, row_span),
                        col_span=max(1, col_span),
                        is_header=is_th,
                        header_scope=scope,
                        text=c_text,
                        bbox=cell_bbox
                    ))

            if cells:
                table_data = TableModel(
                    bbox=bbox,
                    rows_count=len(table_rows),
                    cols_count=max((len(r.get("cells", []) or []) for r in table_rows), default=1),
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

    def _convert_list_kid(
        self,
        kid: Dict[str, Any],
        page_idx: int,
        k_idx: int,
        page_w: float,
        page_h: float
    ) -> List[SemanticElement]:
        """Converts an ODL list container into Lbl/LBody element pairs so the
        tagger can build <L> -> <LI> -> <Lbl>/<LBody> structure."""
        elements: List[SemanticElement] = []
        raw_box = kid.get("bounding box", [0, 0, 100, 100])
        if len(raw_box) == 4:
            x0 = float(raw_box[0])
            y0_raw = float(raw_box[1])
            x1 = float(raw_box[2])
            y1_raw = float(raw_box[3])
            top_y0 = max(0.0, page_h - max(y0_raw, y1_raw))
            top_y1 = min(page_h, page_h - min(y0_raw, y1_raw))
            bbox = BoundingBox(x0=x0, y0=top_y0, x1=x1, y1=top_y1)
        else:
            bbox = BoundingBox(x0=0, y0=0, x1=page_w, y1=page_h)

        items = kid.get("list items", []) or []
        for li_idx, li in enumerate(items):
            li_box = li.get("bounding box") or raw_box
            if len(li_box) == 4:
                lx0 = float(li_box[0])
                ly0_raw = float(li_box[1])
                lx1 = float(li_box[2])
                ly1_raw = float(li_box[3])
                li_bbox = BoundingBox(
                    x0=lx0,
                    y0=max(0.0, page_h - max(ly0_raw, ly1_raw)),
                    x1=lx1,
                    y1=min(page_h, page_h - min(ly0_raw, ly1_raw))
                )
            else:
                li_bbox = bbox

            text = (li.get("content") or "").strip()
            if not text:
                text = "".join(str(sub.get("content") or sub.get("text") or "") for sub in li.get("kids", []) or []).strip()
            if not text:
                continue

            marker_match = re.match(r'^([\u2022\u2013\u2014*\-]|\d{1,3}[.)])\s*(.*)$', text, re.DOTALL)
            if not marker_match:
                elements.append(SemanticElement(
                    id=f"p{page_idx}_odl_{k_idx}_li{li_idx}",
                    tag=StandardTag.LBODY,
                    page_num=page_idx,
                    reading_order_index=k_idx,
                    bbox=li_bbox,
                    text=text,
                    font_size=li.get("font size")
                ))
                continue

            label_str = marker_match.group(1)
            body_text = (marker_match.group(2) or "").strip()
            font_size = li.get("font size") or 10.0
            try:
                font_size = float(font_size)
            except (TypeError, ValueError):
                font_size = 10.0
            label_width = max(10.0, font_size * 0.9, len(label_str) * font_size * 0.55)
            lbl_bbox = BoundingBox(
                x0=li_bbox.x0, y0=li_bbox.y0,
                x1=min(li_bbox.x1, li_bbox.x0 + label_width), y1=li_bbox.y1
            )
            body_bbox = BoundingBox(
                x0=min(li_bbox.x1, li_bbox.x0 + label_width), y0=li_bbox.y0,
                x1=li_bbox.x1, y1=li_bbox.y1
            )
            elements.append(SemanticElement(
                id=f"p{page_idx}_odl_{k_idx}_lbl{li_idx}",
                tag=StandardTag.LBL,
                page_num=page_idx,
                reading_order_index=k_idx,
                bbox=lbl_bbox,
                text=label_str,
                list_label=label_str,
                font_size=font_size
            ))
            elements.append(SemanticElement(
                id=f"p{page_idx}_odl_{k_idx}_lbody{li_idx}",
                tag=StandardTag.LBODY,
                page_num=page_idx,
                reading_order_index=k_idx,
                bbox=body_bbox,
                text=body_text,
                list_label=label_str,
                font_size=font_size
            ))
        return elements

    def _enrich_figure_alt_text(self, elements: List[SemanticElement], page_idx: int):
        """Replaces generic figure alt placeholders with contextual descriptions."""
        try:
            from src.engine.alt_text_gen import AltTextGenerator
            gen = AltTextGenerator()
            for el in elements:
                if el.tag == StandardTag.FIGURE and (not el.alt_text or gen._is_generic_placeholder(el.alt_text)):
                    el.alt_text = gen.generate_alt_text(el, elements, None)
        except Exception:
            pass

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
