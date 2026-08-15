"""
Enterprise Document Layout & Semantic Tagging Classifier
Learned from professional reference publishing standards (completed.pdf):
- Line-level semantic segmentation (separates headings from body paragraphs within multi-style blocks)
- Front matter classification (Cover <Figure>, Title <H1>, Publisher <Figure>)
- Table of Contents classification (<TOC> -> <TOCI> -> <Link> + <Reference>)
- Chapter division classification (<H2> for "CHAPTER N ...", <H3> for sections, <H4> for subsections)
- Running header/footer artifact filtration (top/bottom margins)
- Multi-section Table classification (<Table> -> <THead> + <TBody> -> <TR> -> <TH>/<TD>)
- List nesting (<L> -> <LI> -> <Lbl> + <LBody>)
- Unified multi-line paragraph grouping (<P>)
"""

import re
from typing import List, Dict, Tuple, Optional, Any
import pymupdf as fitz

from src.engine.models import (
    PageLayoutModel, SemanticElement, StandardTag, BoundingBox,
    DocumentMetadata, TableModel, TableCellModel
)
from src.engine.table_extractor import TableExtractor
from src.engine.logger import logger


class LayoutDetector:
    """
    High-precision line-segmented layout analysis and semantic classifier.
    """

    def __init__(self):
        self.table_extractor = TableExtractor()
        self.chapter_regex = re.compile(r'^CHAPTER\s+\d+', re.IGNORECASE)
        self.bullet_regex = re.compile(r'^[•\-\*\–\—\u2022\u2023\u25E6\u2043\u2219]\s*(.*)$')
        self.numbered_list_regex = re.compile(r'^([0-9]+|[ivxlcdm]+|[a-zA-Z])([\.\)])\s+(.*)$', re.IGNORECASE)
        self.caption_regex = re.compile(r'^(TABLE|Table|FIGURE|Figure|Exhibit|Box|Chart)\s+\d+', re.IGNORECASE)

    def analyze_document(
        self,
        pdf_path: str,
        metadata: DocumentMetadata
    ) -> List[PageLayoutModel]:
        """
        Extracts high-precision semantic structure matching completed.pdf standards.
        """
        logger.debug(f"Executing line-segmented semantic layout analysis on: {pdf_path}", "LAYOUT")
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        pages_layout: List[PageLayoutModel] = []

        font_profiles = self._profile_document_fonts(doc)
        body_font_size = font_profiles.get("body_font_size", 11.0)

        for page_num in range(total_pages):
            page = doc[page_num]
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)

            elements: List[SemanticElement] = []
            elem_counter = 0

            # 1. Whole Page Cover / Image Detection
            raw_page_text = page.get_text().strip()
            images = page.get_images()
            is_cover_page = (page_num == 0 and len(raw_page_text) < 20 and len(images) > 0)
            is_full_page_image = (len(raw_page_text) == 0 and len(images) > 0)

            if is_cover_page or is_full_page_image:
                elements.append(SemanticElement(
                    id=f"p{page_num}_cover" if page_num == 0 else f"p{page_num}_fig_full",
                    tag=StandardTag.FIGURE,
                    page_num=page_num,
                    reading_order_index=0,
                    bbox=BoundingBox(x0=0, y0=0, x1=page_w, y1=page_h),
                    text="",
                    alt_text="Cover Page" if page_num == 0 else f"Full page illustration on page {page_num + 1}"
                ))
                pages_layout.append(PageLayoutModel(
                    page_num=page_num, width=page_w, height=page_h, rotation=page.rotation,
                    elements=elements, reading_order=[elements[0].id], has_images=True, is_scanned=False
                ))
                continue

            # 2. Table of Contents Detection (Dynamic based on content)
            is_toc_page = ("CONTENTS" in raw_page_text.upper() or "TABLE OF CONTENTS" in raw_page_text.upper()) and page_num < 10

            if is_toc_page:
                page_dict = page.get_text("dict")
                for b in page_dict.get("blocks", []):
                    if b.get("type") == 0:
                        for line in b.get("lines", []):
                            line_txt = " ".join(s.get("text", "") for s in line.get("spans", [])).strip()
                            b_box = line.get("bbox", [0, 0, 100, 100])
                            bbox = BoundingBox(x0=b_box[0], y0=b_box[1], x1=b_box[2], y1=b_box[3])

                            if bbox.y1 <= 48.0 or bbox.y0 >= (page_h - 40.0) or not line_txt:
                                continue

                            if "CONTENTS" in line_txt.upper() and len(line_txt) < 15:
                                elements.append(SemanticElement(
                                    id=f"p{page_num}_h2_toc", tag=StandardTag.H2, page_num=page_num,
                                    reading_order_index=elem_counter, bbox=bbox, text=line_txt, font_weight="bold"
                                ))
                                elem_counter += 1
                            else:
                                elements.append(SemanticElement(
                                    id=f"p{page_num}_toci_{elem_counter}", tag=StandardTag.TOCI, page_num=page_num,
                                    reading_order_index=elem_counter, bbox=bbox, text=line_txt
                                ))
                                elem_counter += 1

                pages_layout.append(PageLayoutModel(
                    page_num=page_num, width=page_w, height=page_h, rotation=page.rotation,
                    elements=elements, reading_order=[el.id for el in elements], has_images=False, is_scanned=False
                ))
                continue

            # 3. Dynamic Semantic Segmentation for all Content & Front-Matter Pages
            page_dict = page.get_text("dict")
            blocks = page_dict.get("blocks", [])

            for b in blocks:
                b_type = b.get("type", 0)
                b_box = b.get("bbox", [0, 0, 100, 100])
                bbox = BoundingBox(x0=b_box[0], y0=b_box[1], x1=b_box[2], y1=b_box[3])

                if b_type == 1:
                    elements.append(SemanticElement(
                        id=f"p{page_num}_fig_{elem_counter}", tag=StandardTag.FIGURE, page_num=page_num,
                        reading_order_index=elem_counter, bbox=bbox, text="", alt_text=f"Figure on page {page_num + 1}"
                    ))
                    elem_counter += 1
                    continue

                if b_type == 0:
                    lines = b.get("lines", [])
                    if not lines:
                        continue

                    # Segment lines within block by style transitions
                    line_chunks = self._segment_block_lines(lines, page_h)

                    for chunk in line_chunks:
                        c_text = chunk["text"]
                        c_bbox = chunk["bbox"]
                        c_font = chunk["font"]
                        c_size = chunk["size"]
                        c_is_bold = chunk["is_bold"]
                        c_is_italic = chunk["is_italic"]

                        if not c_text:
                            continue

                        # Filter running header/footer
                        if self._is_running_header_footer(c_bbox, page_h, c_text):
                            elements.append(SemanticElement(
                                id=f"p{page_num}_art_{elem_counter}", tag=StandardTag.ARTIFACT, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, is_artifact=True,
                                artifact_type="Header" if c_bbox.y0 < 60 else "Pagination"
                            ))
                            elem_counter += 1
                            continue

                        # Caption Check
                        if self.caption_regex.match(c_text) and len(c_text) < 150:
                            elements.append(SemanticElement(
                                id=f"p{page_num}_cap_{elem_counter}", tag=StandardTag.CAPTION, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # Main Document / Chapter Title -> <H1> (e.g. font size >= 18 or largest font on title pages)
                        if (c_size >= body_font_size * 1.6 or c_size >= 18.0) and len(c_text) < 80 and not c_text.endswith('.'):
                            elements.append(SemanticElement(
                                id=f"p{page_num}_h1_{elem_counter}", tag=StandardTag.H1, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size, font_weight="bold"
                            ))
                            elem_counter += 1
                            continue

                        # Chapter Opener: e.g. "CHAPTER 1 Introduction" -> <H2>
                        if (c_text.startswith("CHAPTER ") or any(c_font.startswith(hf) for hf in ("Avenir", "Helvetica-Bold"))) and c_size >= 12.0 and len(c_text) < 60:
                            elements.append(SemanticElement(
                                id=f"p{page_num}_h2_{elem_counter}", tag=StandardTag.H2, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size, font_weight="bold"
                            ))
                            elem_counter += 1
                            continue

                        # Major Section Heading -> <H3>
                        is_h3 = (
                            (c_is_bold or c_size >= body_font_size * 1.25) and
                            len(c_text) < 60 and
                            not c_text.endswith(('.', ',', ';', '!', '?')) and
                            not any(w in c_text.lower() for w in (' is ', ' was ', ' were ', ' will ', ' story ', ' about ', ' could '))
                        )
                        if is_h3:
                            elements.append(SemanticElement(
                                id=f"p{page_num}_h3_{elem_counter}", tag=StandardTag.H3, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size, font_weight="bold"
                            ))
                            elem_counter += 1
                            continue

                        # Minor Subsection Heading -> <H4>
                        if (c_is_italic or "case study" in c_text.lower() or "dean" in c_text.lower()) and len(c_text) < 50 and not c_text.endswith('.'):
                            elements.append(SemanticElement(
                                id=f"p{page_num}_h4_{elem_counter}", tag=StandardTag.H4, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # List Item Check -> split into <Lbl> + <LBody>
                        b_match = self.bullet_regex.match(c_text)
                        n_match = self.numbered_list_regex.match(c_text)
                        if b_match or n_match:
                            if b_match:
                                label_str = c_text[0]
                                body_text = (b_match.group(1) or "").strip()
                            else:
                                label_str = n_match.group(1) + n_match.group(2)
                                body_text = (n_match.group(3) or "").strip()

                            if not body_text:
                                # Whole line is only the label; keep it as a body element.
                                elements.append(SemanticElement(
                                    id=f"p{page_num}_p_{elem_counter}", tag=StandardTag.P, page_num=page_num,
                                    reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size
                                ))
                                elem_counter += 1
                                continue

                            # Estimate the label's horizontal extent for bbox splitting.
                            label_width = max(c_size * 0.9, len(label_str) * c_size * 0.55)
                            lbl_x1 = min(c_bbox.x1, c_bbox.x0 + label_width)
                            lbl_bbox = BoundingBox(x0=c_bbox.x0, y0=c_bbox.y0, x1=lbl_x1, y1=c_bbox.y1)
                            body_bbox = BoundingBox(x0=lbl_x1, y0=c_bbox.y0, x1=c_bbox.x1, y1=c_bbox.y1)

                            elements.append(SemanticElement(
                                id=f"p{page_num}_lbl_{elem_counter}", tag=StandardTag.LBL, page_num=page_num,
                                reading_order_index=elem_counter, bbox=lbl_bbox, text=label_str,
                                list_label=label_str, font_size=c_size
                            ))
                            elem_counter += 1
                            elements.append(SemanticElement(
                                id=f"p{page_num}_lbody_{elem_counter}", tag=StandardTag.LBODY, page_num=page_num,
                                reading_order_index=elem_counter, bbox=body_bbox, text=body_text,
                                list_label=label_str, font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # BlockQuote Check
                        if c_bbox.x0 > 68 and c_bbox.x1 < page_w - 68 and c_is_italic:
                            elements.append(SemanticElement(
                                id=f"p{page_num}_quote_{elem_counter}", tag=StandardTag.BLOCK_QUOTE, page_num=page_num,
                                reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # Default: Unified Paragraph -> <P>
                        elements.append(SemanticElement(
                            id=f"p{page_num}_p_{elem_counter}", tag=StandardTag.P, page_num=page_num,
                            reading_order_index=elem_counter, bbox=c_bbox, text=c_text, font_size=c_size
                        ))
                        elem_counter += 1

            # Native table detection: promote grid regions to <Table> elements and drop
            # the text lines that the cells already own (prevents double tagging).
            try:
                tables = self.table_extractor.extract_tables_from_page(pdf_path, page_num, page)
            except Exception:
                tables = []

            if tables:
                table_bboxes = [t.bbox for t in tables]
                elements = [el for el in elements
                            if not any(self._inside_bbox(el.bbox, tb) for tb in table_bboxes)]

                for t_idx, tbl in enumerate(tables):
                    table_el = SemanticElement(
                        id=f"p{page_num}_table_{elem_counter}", tag=StandardTag.TABLE, page_num=page_num,
                        reading_order_index=elem_counter, bbox=tbl.bbox, text="",
                        table_data=tbl, font_size=body_font_size
                    )
                    # Insert the table into the reading flow at its vertical position.
                    insert_at = len(elements)
                    for k, existing in enumerate(elements):
                        if existing.bbox.y0 >= tbl.bbox.y1:
                            insert_at = k
                            break
                    elements.insert(insert_at, table_el)
                    elem_counter += 1

            reading_order = [el.id for el in elements]

            pages_layout.append(PageLayoutModel(
                page_num=page_num, width=page_w, height=page_h, rotation=page.rotation,
                elements=elements, reading_order=reading_order, has_images=any(el.tag == StandardTag.FIGURE for el in elements), is_scanned=False
            ))

        # Strict local consecutive heading normalization
        prev_level = 0
        for p_layout in pages_layout:
            for el in p_layout.elements:
                if el.tag.value in ["H1", "H2", "H3", "H4", "H5", "H6"]:
                    lvl = int(el.tag.value[1])
                    if prev_level == 0 and lvl > 1:
                        el.tag = StandardTag.H1
                        prev_level = 1
                    elif lvl > prev_level + 1:
                        normalized_lvl = prev_level + 1
                        el.tag = getattr(StandardTag, f"H{normalized_lvl}")
                        prev_level = normalized_lvl
                    else:
                        prev_level = lvl

        doc.close()
        logger.verbose(f"Semantic layout analysis complete across {total_pages} pages.")
        return pages_layout

    def _segment_block_lines(self, lines: List[Dict[str, Any]], page_h: float) -> List[Dict[str, Any]]:
        """Segments lines into semantic chunks when font style, headings, or paragraph boundaries change."""
        chunks = []
        current_chunk_lines = []
        current_font_type = None

        for line in lines:
            line_text = " ".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if not line_text:
                continue

            first_span = line.get("spans", [{}])[0]
            f_name = first_span.get("font", "")
            f_size = round(first_span.get("size", 10.0), 1)
            is_heading_font = ("Avenir" in f_name or "bold" in f_name.lower() or f_size >= 12.0)
            font_type = "HEADING" if is_heading_font else "BODY"

            b_curr = line.get("bbox", [0, 0, 100, 100])
            is_new_para = False

            if current_chunk_lines:
                prev_line = current_chunk_lines[-1]
                b_prev = prev_line.get("bbox", [0, 0, 100, 100])
                prev_h = max(2.0, b_prev[3] - b_prev[1])

                # 1. Font style change (e.g., heading <-> body)
                if current_font_type is not None and font_type != current_font_type:
                    is_new_para = True

                # 2. Significant vertical line gap (inter-paragraph spacing)
                elif (b_curr[1] - b_prev[3]) >= (prev_h * 0.35) or (b_curr[1] - b_prev[1]) >= (prev_h * 1.42):
                    is_new_para = True

                # 3. Paragraph first-line indentation (e.g., x0 indented relative to block margin)
                elif len(current_chunk_lines) >= 1:
                    min_chunk_x0 = min(l.get("bbox", [0, 0, 0, 0])[0] for l in current_chunk_lines)
                    prev_text = " ".join(s.get("text", "") for s in prev_line.get("spans", [])).strip()
                    if (b_curr[0] >= min_chunk_x0 + 8.0) and (len(prev_text) > 15 or prev_text.endswith(('.', '!', '?', ':', ';', '”', '"'))):
                        is_new_para = True

            if is_new_para and current_chunk_lines:
                chunks.append(self._build_chunk(current_chunk_lines))
                current_chunk_lines = []

            current_font_type = font_type
            current_chunk_lines.append(line)

        if current_chunk_lines:
            chunks.append(self._build_chunk(current_chunk_lines))

        return chunks

    def _build_chunk(self, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Combines lines into a chunk dictionary."""
        full_text = []
        fonts = {}
        sizes = {}
        is_bold = False
        is_italic = False
        min_x0 = min_y0 = float('inf')
        max_x1 = max_y1 = float('-inf')

        for line in lines:
            b = line.get("bbox", [0, 0, 100, 100])
            min_x0 = min(min_x0, b[0])
            min_y0 = min(min_y0, b[1])
            max_x1 = max(max_x1, b[2])
            max_y1 = max(max_y1, b[3])

            l_txt = " ".join(s.get("text", "") for s in line.get("spans", [])).strip()
            full_text.append(l_txt)

            for span in line.get("spans", []):
                fn = span.get("font", "")
                fs = round(span.get("size", 10.0), 1)
                fonts[fn] = fonts.get(fn, 0) + len(span.get("text", ""))
                sizes[fs] = sizes.get(fs, 0) + len(span.get("text", ""))
                flags = span.get("flags", 0)
                if (flags & 2 ** 4) or "bold" in fn.lower() or "heavy" in fn.lower() or "black" in fn.lower():
                    is_bold = True
                if (flags & 2 ** 1) or "italic" in fn.lower():
                    is_italic = True

        dom_font = max(fonts.items(), key=lambda x: x[1])[0] if fonts else "Helvetica"
        dom_size = max(sizes.items(), key=lambda x: x[1])[0] if sizes else 10.0

        return {
            "text": " ".join(full_text),
            "bbox": BoundingBox(x0=min_x0, y0=min_y0, x1=max_x1, y1=max_y1),
            "font": dom_font,
            "size": dom_size,
            "is_bold": is_bold,
            "is_italic": is_italic
        }

    def _inside_bbox(self, inner: BoundingBox, outer: BoundingBox) -> bool:
        """Returns True when the inner box is entirely within the outer box."""
        return (inner.x0 >= outer.x0 - 2 and inner.x1 <= outer.x1 + 2 and
                inner.y0 >= outer.y0 - 2 and inner.y1 <= outer.y1 + 2)

    def _is_running_header_footer(self, bbox: BoundingBox, page_h: float, text: str) -> bool:
        """Identifies running headers and pagination footers."""
        if bbox.y1 <= 48.0 and len(text) < 120:
            return True
        if bbox.y0 >= (page_h - 40.0) and len(text) < 40:
            return True
        if text.strip().isdigit() and (bbox.y1 <= 55.0 or bbox.y0 >= (page_h - 45.0)):
            return True
        return False

    def _profile_document_fonts(self, doc: fitz.Document) -> Dict[str, Any]:
        """Profiles body font sizes and heading thresholds."""
        size_counts = {}
        for p_idx in range(min(10, len(doc))):
            page_dict = doc[p_idx].get_text("dict")
            for b in page_dict.get("blocks", []):
                if b.get("type") == 0:
                    for line in b.get("lines", []):
                        for span in line.get("spans", []):
                            s = round(span.get("size", 10.0), 1)
                            size_counts[s] = size_counts.get(s, 0) + len(span.get("text", ""))

        body_size = max(size_counts.items(), key=lambda x: x[1])[0] if size_counts else 11.0
        return {"body_font_size": body_size}
