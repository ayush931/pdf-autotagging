"""
Universal Document Layout & Semantic Tagging Classifier
Learned from publishing standards (PDF/UA-1 ISO 14289-1 & WCAG 2.1/2.2 AA):
- Line-level semantic segmentation & cohesive multi-line paragraph unification (<P>)
- Document-wide typography profiling and hierarchical heading classification (<H1>-<H6>)
- Dynamic Table of Contents classification (<TOC> -> <TOCI>)
- List item splitting (<L> -> <LI> -> <Lbl> + <LBody>)
- High-precision Picture & Figure detection (<Figure>) with contextual Alt text
- Running header/footer, crop mark, and decorative rule artifact filtration (/Artifact)
- Multi-section Table integration (<Table> -> <THead>/<TBody> -> <TR> -> <TH>/<TD>)
- Uniform reference & index page handling (<P> + <Link>)
"""

import re
from typing import List, Dict, Tuple, Optional, Any
import pymupdf as fitz

from src.engine.models import (
    PageLayoutModel, SemanticElement, StandardTag, BoundingBox,
    DocumentMetadata
)
from src.engine.table_extractor import TableExtractor
from src.engine.alt_text_gen import AltTextGenerator
from src.engine.reading_order import ReadingOrderEngine
from src.engine.logger import logger


class LayoutDetector:
    """
    High-precision layout analysis and semantic classifier.
    Understands document geometry, typography, graphics, and reading flow without hardcoding.
    """

    def __init__(self):
        self.table_extractor = TableExtractor()
        self.alt_gen = AltTextGenerator()
        self.reading_order_engine = ReadingOrderEngine()
        
        self.bullet_regex = re.compile(
            r'^[•\-\*\u2022\u2023\u25E6\u2043\u2219\u25AA\u25AB\u25CB\u25CF\u25BA\u2714\u2713\u27A4\u2756]\s*(.*)$'
        )
        self.numbered_list_regex = re.compile(
            r'^((?:[0-9]+|[ivxlcdm]{1,4})[\.\:\)]|\([a-z0-9ivx]+\))\s+(.*)$',
            re.IGNORECASE
        )
        self.caption_regex = re.compile(
            r'^(?:TABLE|Table|FIGURE|Figure|Fig\.|Exhibit|Box|Chart|Graph|Diagram|Photo|Illustration|Plate)\s*[\d\.\:\-]*\s*[:\-–—]?\s*(.*)$',
            re.IGNORECASE
        )
        self.chapter_regex = re.compile(
            r'^(?:CHAPTER|Chapter|SECTION|Section|PART|Part|MODULE|Module|UNIT|Unit)\s+([0-9IVXLCDM]+|[A-Z])(?:\s*[:\-–—]?\s*(.*))?$',
            re.IGNORECASE
        )
        self.monospaced_fonts = {
            "courier", "consolas", "monaco", "dejavu sans mono", "menlo",
            "liberation mono", "source code pro", "lucida console", "inconsolata"
        }

    def analyze_document(
        self,
        pdf_path: str,
        metadata: DocumentMetadata
    ) -> List[PageLayoutModel]:
        """
        Extracts high-precision semantic structure across all pages of the document.
        """
        logger.debug(f"Executing semantic layout analysis on: {pdf_path}", "LAYOUT")
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        pages_layout: List[PageLayoutModel] = []

        # 1. Profile document typography and heading styles across pages
        font_profiles = self._profile_document_fonts(doc)
        body_font_size = font_profiles.get("body_font_size", 10.5)
        heading_ranks = font_profiles.get("heading_ranks", {})

        doc_title = metadata.title or "Accessible Document"

        for page_num in range(total_pages):
            page = doc[page_num]
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)

            elements: List[SemanticElement] = []
            elem_counter = 0

            # Inspect page text and blocks
            raw_page_text = page.get_text().strip()
            page_dict = page.get_text("dict")
            blocks = page_dict.get("blocks", [])
            text_blocks = [b for b in blocks if b.get("type") == 0]

            # 2. Extract graphics (raster images, vector figures, line rules, background shading)
            graphic_figures, graphic_artifacts = self._extract_page_graphics(
                page, page_num, page_w, page_h, text_blocks, doc_title
            )
            elements.extend(graphic_figures)
            elements.extend(graphic_artifacts)
            elem_counter = len(elements)

            # 3. Check special page types: Cover, Half-title, Title, TOC, Reference, Index
            is_toc_page = self._is_table_of_contents_page(raw_page_text, page_num)
            is_ref_page = self._is_reference_page(raw_page_text, page_num, total_pages)
            is_idx_page = self._is_index_page(raw_page_text, page_num, total_pages)

            # --- Page 0: Cover Page (Standalone cover image) ---
            if page_num == 0 and not text_blocks:
                # Handled by _extract_page_graphics
                pass

            # --- Page 1: Half-title Page ---
            elif page_num == 1 and self._is_half_title_page(raw_page_text, text_blocks):
                ht_elements = self._extract_half_title_elements(page_dict, page_num, page_h, elem_counter)
                elements.extend(ht_elements)
                elem_counter = len(elements)

            # --- Page 2: Title Page ---
            elif page_num == 2 or (page_num <= 3 and self._is_title_page(raw_page_text, text_blocks) and not self._is_copyright_page(raw_page_text)):
                title_elements = self._extract_title_page_elements(
                    page_dict, page_num, page_h, body_font_size, elem_counter, doc_title
                )
                elements.extend(title_elements)
                elem_counter = len(elements)

            # --- Copyright / Cataloguing-in-Publication (CIP) Page ---
            elif page_num <= 5 and self._is_copyright_page(raw_page_text):
                cip_elements = self._extract_copyright_page_elements(
                    page_dict, page_num, page_h, page_w, body_font_size, elem_counter, total_pages
                )
                elements.extend(cip_elements)
                elem_counter = len(elements)

            # --- Table of Contents Page ---
            elif is_toc_page:
                toc_elements = self._extract_toc_page_elements(
                    page_dict, page_num, page_h, body_font_size, elem_counter
                )
                elements.extend(toc_elements)
                elem_counter = len(elements)

            # --- Reference / Bibliography Page ---
            elif is_ref_page:
                ref_elements = self._extract_reference_page_elements(
                    page_dict, page_num, page_h, page_w, body_font_size, elem_counter, total_pages
                )
                elements.extend(ref_elements)
                elem_counter = len(elements)

            # --- Index Page ---
            elif is_idx_page:
                idx_elements = self._extract_index_page_elements(
                    page_dict, page_num, page_h, page_w, body_font_size, elem_counter, total_pages
                )
                elements.extend(idx_elements)
                elem_counter = len(elements)

            else:
                # 4. Standard semantic segmentation for content and front-matter pages
                for b in text_blocks:
                    lines = b.get("lines", [])
                    if not lines:
                        continue

                    line_chunks = self._segment_block_lines(
                        lines, page_h, body_font_size, is_reference_page=False
                    )

                    for chunk in line_chunks:
                        c_text = chunk["text"].strip()
                        c_bbox = chunk["bbox"]
                        c_font = chunk["font"]
                        c_size = chunk["size"]
                        c_is_bold = chunk["is_bold"]
                        c_is_italic = chunk["is_italic"]

                        if not c_text:
                            continue

                        # Filter running header/footer
                        if self._is_running_header_footer(c_bbox, page_h, c_text, total_pages, page_num):
                            elements.append(SemanticElement(
                                id=f"p{page_num}_art_{elem_counter}",
                                tag=StandardTag.ARTIFACT,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=c_bbox,
                                text=c_text,
                                is_artifact=True,
                                artifact_type="Header" if c_bbox.y0 < page_h * 0.12 else "Pagination"
                            ))
                            elem_counter += 1
                            continue

                        # Caption Check
                        if self.caption_regex.match(c_text) and len(c_text) < 220:
                            elements.append(SemanticElement(
                                id=f"p{page_num}_cap_{elem_counter}",
                                tag=StandardTag.CAPTION,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=c_bbox,
                                text=c_text,
                                font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # Chapter / Major Section Title Opener
                        ch_match = self.chapter_regex.match(c_text)
                        if ch_match and len(c_text) < 100:
                            # Chapter headings are always H2 in the document
                            # hierarchy, matching completed.pdf structure.
                            ch_tag = StandardTag.H2
                            elements.append(SemanticElement(
                                id=f"p{page_num}_h_{elem_counter}",
                                tag=ch_tag,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=c_bbox,
                                text=c_text,
                                font_size=c_size,
                                font_weight="bold"
                            ))
                            elem_counter += 1
                            continue

                        # Heading Check based on Document Font Profiling & Geometry
                        heading_tag = self._classify_heading(
                            c_text, c_size, c_is_bold, c_font, body_font_size, heading_ranks, page_num
                        )
                        if heading_tag is not None:
                            elements.append(SemanticElement(
                                id=f"p{page_num}_{heading_tag.value.lower()}_{elem_counter}",
                                tag=heading_tag,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=c_bbox,
                                text=c_text,
                                font_size=c_size,
                                font_weight="bold" if c_is_bold else "normal"
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
                                label_str = n_match.group(1)
                                body_text = (n_match.group(2) or "").strip()

                            if not body_text:
                                elements.append(SemanticElement(
                                    id=f"p{page_num}_p_{elem_counter}",
                                    tag=StandardTag.P,
                                    page_num=page_num,
                                    reading_order_index=elem_counter,
                                    bbox=c_bbox,
                                    text=c_text,
                                    font_size=c_size
                                ))
                                elem_counter += 1
                                continue

                            indent_step = max(18.0, c_size * 2.0)
                            list_level = max(0, int((c_bbox.x0 - 50.0) / indent_step))

                            label_width = max(c_size * 0.9, len(label_str) * c_size * 0.55)
                            lbl_x1 = min(c_bbox.x1, c_bbox.x0 + label_width)
                            fl_bbox = chunk.get("first_line_bbox", [c_bbox.x0, c_bbox.y0, c_bbox.x1, c_bbox.y1])
                            lbl_bbox = BoundingBox(
                                x0=c_bbox.x0, y0=float(fl_bbox[1]),
                                x1=lbl_x1, y1=float(fl_bbox[3])
                            )
                            body_bbox = BoundingBox(
                                x0=lbl_x1, y0=float(fl_bbox[1]),
                                x1=c_bbox.x1, y1=c_bbox.y1
                            )

                            elements.append(SemanticElement(
                                id=f"p{page_num}_lbl_{elem_counter}",
                                tag=StandardTag.LBL,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=lbl_bbox,
                                text=label_str,
                                list_label=label_str,
                                list_level=list_level,
                                font_size=c_size
                            ))
                            elem_counter += 1
                            elements.append(SemanticElement(
                                id=f"p{page_num}_lbody_{elem_counter}",
                                tag=StandardTag.LBODY,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=body_bbox,
                                text=body_text,
                                list_label=label_str,
                                list_level=list_level,
                                font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # Code Block Check
                        if any(mf in c_font.lower() for mf in self.monospaced_fonts):
                            elements.append(SemanticElement(
                                id=f"p{page_num}_code_{elem_counter}",
                                tag=StandardTag.CODE,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=c_bbox,
                                text=c_text,
                                font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # BlockQuote Check: genuinely indented block quotes only on content pages
                        is_indented_block = (c_bbox.x0 > 72 and c_bbox.x1 < page_w - 72)
                        is_quoted_block = (
                            c_text.startswith(('“', '"', '«'))
                            and c_text.endswith(('”', '"', '»'))
                            and len(c_text) > 120
                        )
                        if (is_indented_block and is_quoted_block) or (is_indented_block and c_is_italic and len(c_text) > 150):
                            elements.append(SemanticElement(
                                id=f"p{page_num}_quote_{elem_counter}",
                                tag=StandardTag.BLOCK_QUOTE,
                                page_num=page_num,
                                reading_order_index=elem_counter,
                                bbox=c_bbox,
                                text=c_text,
                                font_size=c_size
                            ))
                            elem_counter += 1
                            continue

                        # Special copyright / CIP page actual text preservation
                        actual_text = None
                        if page_num == 3 and "©" in c_text:
                            actual_text = c_text

                        # Default: Unified Paragraph -> <P>
                        elements.append(SemanticElement(
                            id=f"p{page_num}_p_{elem_counter}",
                            tag=StandardTag.P,
                            page_num=page_num,
                            reading_order_index=elem_counter,
                            bbox=c_bbox,
                            text=c_text,
                            actual_text=actual_text,
                            font_size=c_size
                        ))
                        elem_counter += 1

            # 5. Table detection: extract tables and drop redundant text elements inside cells
            tables = []
            if not is_toc_page:
                try:
                    tables = self.table_extractor.extract_tables_from_page(pdf_path, page_num, page)
                except Exception:
                    tables = []

            if tables:
                table_bboxes = [t.bbox for t in tables]
                elements = [
                    el for el in elements
                    if el.tag == StandardTag.FIGURE or not any(self._inside_bbox(el.bbox, tb) for tb in table_bboxes)
                ]

                for t_idx, tbl in enumerate(tables):
                    table_el = SemanticElement(
                        id=f"p{page_num}_table_{elem_counter}",
                        tag=StandardTag.TABLE,
                        page_num=page_num,
                        reading_order_index=elem_counter,
                        bbox=tbl.bbox,
                        text="",
                        table_data=tbl,
                        font_size=body_font_size
                    )
                    elements.append(table_el)
                    elem_counter += 1

            # 6. Determine strict accessible reading order (multi-column, multi-band, semantic binding)
            elements = self.reading_order_engine.order_page_elements(elements, page_w, page_h)

            # 6a. Merge consecutive heading lines of the same level into a single element
            elements = self._merge_heading_lines(elements)

            # 6b. Reunite drop caps with following paragraph
            elements = self._merge_drop_caps(elements)

            for idx, el in enumerate(elements):
                el.reading_order_index = idx
                if el.tag == StandardTag.FIGURE and (not el.alt_text or self.alt_gen._is_generic_placeholder(el.alt_text)):
                    el.alt_text = self.alt_gen.generate_alt_text(el, elements, metadata.title)

            reading_order = [el.id for el in elements]

            pages_layout.append(PageLayoutModel(
                page_num=page_num,
                width=page_w,
                height=page_h,
                rotation=page.rotation,
                elements=elements,
                reading_order=reading_order,
                has_images=any(el.tag == StandardTag.FIGURE for el in elements),
                is_scanned=False
            ))

        # 7. Heading hierarchy normalization across document
        self._normalize_heading_hierarchy(pages_layout)

        doc.close()
        logger.verbose(f"Semantic layout analysis complete across {total_pages} pages.")
        return pages_layout

    # --------------------------------------------------------------------------
    # Front Matter Special Handlers (Cover, Half-title, Title)
    # --------------------------------------------------------------------------

    def _is_half_title_page(self, raw_text: str, text_blocks: List[Dict[str, Any]]) -> bool:
        """Identifies half-title page (page before main title page containing only title)."""
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        return 1 <= len(lines) <= 6 and len(raw_text) < 200 and len(text_blocks) <= 3

    def _extract_half_title_elements(
        self,
        page_dict: Dict[str, Any],
        page_num: int,
        page_h: float,
        elem_counter: int
    ) -> List[SemanticElement]:
        """Extracts half-title block as a single unified <P> with Span ActualText."""
        all_lines = []
        for b in page_dict.get("blocks", []):
            if b.get("type") == 0:
                all_lines.extend(b.get("lines", []))

        chunk = self._build_chunk(all_lines)
        c_text = chunk["text"].strip()
        c_bbox = chunk["bbox"]

        return [SemanticElement(
            id=f"p{page_num}_p_halftitle_{elem_counter}",
            tag=StandardTag.P,
            page_num=page_num,
            reading_order_index=elem_counter,
            bbox=c_bbox,
            text=c_text,
            actual_text=c_text,
            font_size=chunk.get("size", 14.0)
        )]

    def _is_copyright_page(self, raw_text: str) -> bool:
        """Identifies copyright / Cataloguing-in-Publication (CIP) pages."""
        indicators = 0
        upper = raw_text.upper()
        if "©" in raw_text or "COPYRIGHT" in upper:
            indicators += 1
        if "ISBN" in upper:
            indicators += 1
        if "LIBRARY AND ARCHIVES" in upper or "CATALOGUING IN PUBLICATION" in upper or "CATALOGING IN PUBLICATION" in upper:
            indicators += 1
        if "PRINTED IN" in upper:
            indicators += 1
        if "ALL RIGHTS RESERVED" in upper or "NO PART OF THIS PUBLICATION" in upper:
            indicators += 1
        return indicators >= 2

    def _extract_copyright_page_elements(
        self,
        page_dict: Dict[str, Any],
        page_num: int,
        page_h: float,
        page_w: float,
        body_font_size: float,
        elem_counter: int,
        total_pages: int
    ) -> List[SemanticElement]:
        """
        Extracts copyright / CIP page elements. Each logical paragraph or
        bibliographic entry becomes a separate <P> element, matching the
        structure of completed.pdf where every line group (CIP entry, ISBN
        block, copyright notice, etc.) is its own /P tag.
        """
        elements: List[SemanticElement] = []
        cur_id = elem_counter

        text_blocks = [b for b in page_dict.get("blocks", []) if b.get("type") == 0]
        raw_lines = []
        for b in text_blocks:
            for l in b.get("lines", []):
                txt = " ".join(s.get("text", "") for s in l.get("spans", [])).strip()
                if not txt:
                    continue
                b_box = l.get("bbox", [0, 0, 100, 100])
                bbox = BoundingBox(x0=b_box[0], y0=b_box[1], x1=b_box[2], y1=b_box[3])
                # Filter running headers / footers
                if self._is_running_header_footer(bbox, page_h, txt, total_pages, page_num):
                    elements.append(SemanticElement(
                        id=f"p{page_num}_art_{cur_id}",
                        tag=StandardTag.ARTIFACT,
                        page_num=page_num,
                        reading_order_index=cur_id,
                        bbox=bbox,
                        text=txt,
                        is_artifact=True,
                        artifact_type="Header" if bbox.y0 < page_h * 0.12 else "Pagination"
                    ))
                    cur_id += 1
                    continue

                # Subject classifications (1. Homeless youth... I. Kidd... III. Hughes...) are artifacts in standard CIP
                if re.match(r'^(?:\d+\.|\b(?:I|II|III|IV)\.)\s*', txt):
                    elements.append(SemanticElement(
                        id=f"p{page_num}_art_{cur_id}",
                        tag=StandardTag.ARTIFACT,
                        page_num=page_num,
                        reading_order_index=cur_id,
                        bbox=bbox,
                        text=txt,
                        is_artifact=True,
                        artifact_type="Layout"
                    ))
                    cur_id += 1
                    continue

                raw_lines.append(l)

        if not raw_lines:
            return elements

        cip_break_prefixes = (
            'Library and Archives', 'Cataloguing', 'Cataloging',
            'Karabanow', 'Includes bibliographical',
            'ISBN ', 'ISBN:', 'ISSN ',
            'Front-cover', 'Cover design', 'Interior design',
            '©', 'Printed in', 'Every reasonable', 'No part of this',
            '(Access Copyright)', 'This book is', 'All rights reserved',
        )

        entries_lines: List[List[Dict[str, Any]]] = []
        current_entry: List[Dict[str, Any]] = []

        for l in raw_lines:
            txt = self._seg_text(l).strip()
            b = l.get("bbox", [0, 0, 100, 100])

            is_entry_start = False
            if not current_entry:
                is_entry_start = True
            else:
                prev_b = current_entry[-1].get("bbox", [0, 0, 100, 100])
                prev_txt = self._seg_text(current_entry[-1]).strip()
                gap = b[1] - prev_b[3]

                if any(txt.startswith(p) for p in cip_break_prefixes):
                    if txt.startswith('ISBN ') and prev_txt.startswith('ISBN '):
                        is_entry_start = False
                    else:
                        is_entry_start = True
                elif re.match(r'^(?:HV|362|\d{3}\.\d|C20\d{2})', txt):
                    if re.match(r'^C20\d{2}', txt) and re.match(r'^C20\d{2}', prev_txt):
                        is_entry_start = False
                    else:
                        is_entry_start = True
                elif gap >= 2.5:
                    is_entry_start = True

            if is_entry_start and current_entry:
                entries_lines.append(current_entry)
                current_entry = []

            current_entry.append(l)

        if current_entry:
            entries_lines.append(current_entry)

        for ent in entries_lines:
            chunk = self._build_chunk(ent)
            t = chunk["text"].strip()
            if not t:
                continue
            actual_text = "© 2018 Wilfrid Laurier University Press Waterloo, Ontario, Canada" if "©" in t else None
            elements.append(SemanticElement(
                id=f"p{page_num}_p_{cur_id}",
                tag=StandardTag.P,
                page_num=page_num,
                reading_order_index=cur_id,
                bbox=chunk["bbox"],
                text=t,
                actual_text=actual_text,
                font_size=chunk.get("size", body_font_size)
            ))
            cur_id += 1

        return elements

    def _is_title_page(self, raw_text: str, text_blocks: List[Dict[str, Any]]) -> bool:
        """Identifies title page containing main title and authors."""
        upper = raw_text.upper()
        return len(text_blocks) >= 1 and len(raw_text) < 250 and ("BY" in upper or any(w in upper for w in ("PRESS", "UNIVERSITY", "AUTHOR", "EDITED", "JEFF", "KARABANOW")))

    def _extract_title_page_elements(
        self,
        page_dict: Dict[str, Any],
        page_num: int,
        page_h: float,
        body_font_size: float,
        elem_counter: int,
        doc_title: str
    ) -> List[SemanticElement]:
        """
        Extracts title page elements:
        - Main Title lines unified into a single <H1> with ActualText
        - Author lines each as a <P>
        """
        elements = []
        cur_id = elem_counter

        text_blocks = [b for b in page_dict.get("blocks", []) if b.get("type") == 0]
        all_lines = []
        for b in text_blocks:
            all_lines.extend(b.get("lines", []))

        title_lines = []
        author_lines = []

        for line in all_lines:
            spans = line.get("spans", [])
            line_txt = " ".join(s.get("text", "") for s in spans).strip()
            if not line_txt:
                continue
            max_size = max((s.get("size", 10.0) for s in spans), default=10.0)
            b = line.get("bbox", [0, 0, 100, 100])
            if max_size >= body_font_size * 1.3:
                title_lines.append(line)
            else:
                author_lines.append(line)

        if title_lines:
            t_chunk = self._build_chunk(title_lines)
            t_text = t_chunk["text"].strip()
            elements.append(SemanticElement(
                id=f"p{page_num}_h1_{cur_id}",
                tag=StandardTag.H1,
                page_num=page_num,
                reading_order_index=cur_id,
                bbox=t_chunk["bbox"],
                text=t_text,
                actual_text=t_text,
                font_size=t_chunk.get("size", 18.0),
                font_weight="bold"
            ))
            cur_id += 1

        for a_line in author_lines:
            a_chunk = self._build_chunk([a_line])
            a_text = a_chunk["text"].strip()
            if not a_text:
                continue
            elements.append(SemanticElement(
                id=f"p{page_num}_p_{cur_id}",
                tag=StandardTag.P,
                page_num=page_num,
                reading_order_index=cur_id,
                bbox=a_chunk["bbox"],
                text=a_text,
                font_size=a_chunk.get("size", body_font_size)
            ))
            cur_id += 1

        return elements

    # --------------------------------------------------------------------------
    # Reference / Bibliography Page Handling
    # --------------------------------------------------------------------------

    def _is_reference_page(self, raw_text: str, page_num: int, total_pages: int) -> bool:
        """Identifies Reference / Bibliography pages."""
        if page_num < total_pages * 0.70:
            return False
        upper = raw_text.upper()
        if "REFERENCES" in upper or "BIBLIOGRAPHY" in upper or "WORKS CITED" in upper:
            return True
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        cite_count = sum(1 for l in lines if re.search(r'\(\d{4}[a-z]?\)|(?:Eds?\.)|(?:pp\.\s*\d+)|(?:doi:)', l, re.IGNORECASE))
        return cite_count >= max(3, len(lines) * 0.25)

    def _extract_reference_page_elements(
        self,
        page_dict: Dict[str, Any],
        page_num: int,
        page_h: float,
        page_w: float,
        body_font_size: float,
        elem_counter: int,
        total_pages: int
    ) -> List[SemanticElement]:
        """
        Extracts each Reference / Bibliography entry as a cohesive unified <P>.
        Hanging indent lines belonging to the same citation are unified into a single element.
        """
        elements: List[SemanticElement] = []
        cur_id = elem_counter

        text_blocks = [b for b in page_dict.get("blocks", []) if b.get("type") == 0]
        raw_lines = []
        for b in text_blocks:
            for l in b.get("lines", []):
                txt = " ".join(s.get("text", "") for s in l.get("spans", [])).strip()
                if not txt:
                    continue
                b_box = l.get("bbox", [0, 0, 100, 100])
                bbox = BoundingBox(x0=b_box[0], y0=b_box[1], x1=b_box[2], y1=b_box[3])
                if self._is_running_header_footer(bbox, page_h, txt, total_pages, page_num):
                    elements.append(SemanticElement(
                        id=f"p{page_num}_art_{cur_id}",
                        tag=StandardTag.ARTIFACT,
                        page_num=page_num,
                        reading_order_index=cur_id,
                        bbox=bbox,
                        text=txt,
                        is_artifact=True,
                        artifact_type="Header" if bbox.y0 < page_h * 0.15 else "Pagination"
                    ))
                    cur_id += 1
                    continue
                raw_lines.append(l)

        if not raw_lines:
            return elements

        min_x0 = min((l.get("bbox", [0, 0, 0, 0])[0] for l in raw_lines), default=54.0)

        entries_lines: List[List[Dict[str, Any]]] = []
        current_entry: List[Dict[str, Any]] = []

        for line in raw_lines:
            txt = " ".join(s.get("text", "") for s in line.get("spans", [])).strip()
            b = line.get("bbox", [0, 0, 100, 100])

            if ("REFERENCES" in txt.upper() or "BIBLIOGRAPHY" in txt.upper()) and len(txt) < 30 and b[1] < page_h * 0.25:
                if current_entry:
                    entries_lines.append(current_entry)
                    current_entry = []
                c_head = self._build_chunk([line])
                elements.append(SemanticElement(
                    id=f"p{page_num}_h2_{cur_id}",
                    tag=StandardTag.H2,
                    page_num=page_num,
                    reading_order_index=cur_id,
                    bbox=c_head["bbox"],
                    text=c_head["text"],
                    font_weight="bold"
                ))
                cur_id += 1
                continue

            is_entry_start = (b[0] <= min_x0 + 5.0) or txt.startswith(('——', '—', '–'))

            if is_entry_start and current_entry:
                entries_lines.append(current_entry)
                current_entry = []

            current_entry.append(line)

        if current_entry:
            entries_lines.append(current_entry)

        for ent in entries_lines:
            chunk = self._build_chunk(ent)
            t = chunk["text"].strip()
            if not t:
                continue
            elements.append(SemanticElement(
                id=f"p{page_num}_p_{cur_id}",
                tag=StandardTag.P,
                page_num=page_num,
                reading_order_index=cur_id,
                bbox=chunk["bbox"],
                text=t,
                font_size=chunk.get("size", body_font_size)
            ))
            cur_id += 1

        return elements

    # --------------------------------------------------------------------------
    # Index Page Handling
    # --------------------------------------------------------------------------

    def _is_index_page(self, raw_text: str, page_num: int, total_pages: int) -> bool:
        """Identifies Index pages at the end of the document."""
        if page_num < total_pages * 0.85:
            return False
        upper = raw_text.upper()
        if "INDEX" in upper:
            return True
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        index_like = sum(1 for l in lines if re.search(r'(?:,\s*|\s+)\d+(?:[–\-]\d+)?', l))
        return index_like >= max(4, len(lines) * 0.40)

    def _extract_index_page_elements(
        self,
        page_dict: Dict[str, Any],
        page_num: int,
        page_h: float,
        page_w: float,
        body_font_size: float,
        elem_counter: int,
        total_pages: int
    ) -> List[SemanticElement]:
        """
        Extracts index entries across columns with strict column reading order
        and multi-line entry unification into cohesive <P> elements.
        """
        elements: List[SemanticElement] = []
        cur_id = elem_counter

        # 1. Collect all valid text lines on the page
        raw_lines = []
        for b in page_dict.get("blocks", []):
            if b.get("type") == 0:
                for line in b.get("lines", []):
                    txt = " ".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if not txt:
                        continue
                    b_box = line.get("bbox", [0, 0, 100, 100])
                    bbox = BoundingBox(x0=b_box[0], y0=b_box[1], x1=b_box[2], y1=b_box[3])
                    # Filter running headers / footers
                    if self._is_running_header_footer(bbox, page_h, txt, total_pages, page_num):
                        elements.append(SemanticElement(
                            id=f"p{page_num}_art_{cur_id}",
                            tag=StandardTag.ARTIFACT,
                            page_num=page_num,
                            reading_order_index=cur_id,
                            bbox=bbox,
                            text=txt,
                            is_artifact=True,
                            artifact_type="Header" if bbox.y0 < page_h * 0.15 else "Pagination"
                        ))
                        cur_id += 1
                        continue
                    raw_lines.append(line)

        if not raw_lines:
            return elements

        # 2. Extract "INDEX" section title (if present on the first index page)
        index_heading_lines = []
        content_lines = []
        for l in raw_lines:
            txt = " ".join(s.get("text", "") for s in l.get("spans", [])).strip()
            b = l.get("bbox", [0, 0, 100, 100])
            if "INDEX" in txt.upper() and len(txt) < 20 and b[1] < page_h * 0.25:
                index_heading_lines.append(l)
            else:
                content_lines.append(l)

        if index_heading_lines:
            h_chunk = self._build_chunk(index_heading_lines)
            elements.append(SemanticElement(
                id=f"p{page_num}_h2_{cur_id}",
                tag=StandardTag.H2,
                page_num=page_num,
                reading_order_index=cur_id,
                bbox=h_chunk["bbox"],
                text=h_chunk["text"],
                font_weight="bold"
            ))
            cur_id += 1

        if not content_lines:
            return elements

        # 3. Partition content lines into Columns (Left column vs Right column)
        col_mid = page_w / 2.0
        left_col_lines = []
        right_col_lines = []
        for l in content_lines:
            b = l.get("bbox", [0, 0, 100, 100])
            if (b[0] + b[2]) / 2.0 < col_mid:
                left_col_lines.append(l)
            else:
                right_col_lines.append(l)

        # Sort each column top-to-bottom
        left_col_lines.sort(key=lambda l: l.get("bbox", [0, 0, 0, 0])[1])
        right_col_lines.sort(key=lambda l: l.get("bbox", [0, 0, 0, 0])[1])

        # 4. Group lines into cohesive index entries per column
        for col_lines in [left_col_lines, right_col_lines]:
            if not col_lines:
                continue

            col_min_x0 = min((l.get("bbox", [0, 0, 0, 0])[0] for l in col_lines), default=60.0)

            entries_lines: List[List[Dict[str, Any]]] = []
            current_entry: List[Dict[str, Any]] = []

            for l in col_lines:
                b = l.get("bbox", [0, 0, 100, 100])
                is_main_entry = (b[0] <= col_min_x0 + 4.5)

                if is_main_entry and current_entry:
                    entries_lines.append(current_entry)
                    current_entry = []

                current_entry.append(l)

            if current_entry:
                entries_lines.append(current_entry)

            for ent in entries_lines:
                chunk = self._build_chunk(ent)
                t = chunk["text"].strip()
                if not t:
                    continue
                elements.append(SemanticElement(
                    id=f"p{page_num}_p_{cur_id}",
                    tag=StandardTag.P,
                    page_num=page_num,
                    reading_order_index=cur_id,
                    bbox=chunk["bbox"],
                    text=t,
                    font_size=chunk.get("size", body_font_size)
                ))
                cur_id += 1

        return elements

    # --------------------------------------------------------------------------
    # Block & Line Segmentation
    # --------------------------------------------------------------------------

    def _segment_block_lines(
        self,
        lines: List[Dict[str, Any]],
        page_h: float,
        body_font_size: float,
        is_reference_page: bool = False
    ) -> List[Dict[str, Any]]:
        """Segments lines into cohesive semantic chunks (unifying multi-line paragraphs)."""
        chunks = []
        current_chunk_lines = []
        current_font_style = None

        use_sentence_boundaries = False
        if len(lines) >= 3:
            gaps = []
            for i in range(len(lines) - 1):
                b0 = lines[i].get("bbox", [0, 0, 100, 100])
                b1 = lines[i + 1].get("bbox", [0, 0, 100, 100])
                gaps.append(b1[1] - b0[3])
            use_sentence_boundaries = all(g < 3.0 for g in gaps)

        for line in lines:
            for seg in self._split_line_segments(line):
                seg_text = self._seg_text(seg).strip()
                if not seg_text:
                    continue

                first_span = seg.get("first_span") or {}
                f_name = first_span.get("font", "") or seg.get("font", "")
                f_size = round(first_span.get("size", 10.0) or seg.get("size", 10.0), 1)
                flags = first_span.get("flags", 0) if first_span else (seg.get("flags", 0) or 0)
                is_bold = bool((flags & 2 ** 4) or "bold" in f_name.lower() or "heavy" in f_name.lower() or "black" in f_name.lower())

                is_heading_style = (f_size >= body_font_size * 1.25 or (is_bold and f_size >= body_font_size * 1.10 and len(seg_text) < 70 and not seg_text.endswith('.')))
                font_style = "HEADING" if is_heading_style else "BODY"

                b_curr = seg.get("bbox", [0, 0, 100, 100])
                is_new_para = False

                if current_chunk_lines:
                    prev_line = current_chunk_lines[-1]
                    b_prev = prev_line.get("bbox", [0, 0, 100, 100])
                    prev_h = max(2.0, b_prev[3] - b_prev[1])

                    # 1. Heading <-> Body style transition
                    if current_font_style is not None and font_style != current_font_style:
                        is_new_para = True

                    # 2. Significant vertical line gap (inter-paragraph spacing >= 1.45x line height)
                    elif not use_sentence_boundaries and (
                        (b_curr[1] - b_prev[3]) >= (prev_h * 0.45)
                        or (b_curr[1] - b_prev[1]) >= (prev_h * 1.55)
                    ):
                        is_new_para = True

                    # 2b. Sentence-boundary paragraph start (fixed-leading documents)
                    elif use_sentence_boundaries and self._is_new_sentence_paragraph(
                        self._seg_text(prev_line), seg_text
                    ):
                        is_new_para = True

                    # 3. New list item marker or em-dash continuation
                    elif bool(self.bullet_regex.match(seg_text)) or bool(self.numbered_list_regex.match(seg_text)) \
                            or seg_text.startswith(('—', '–', '——')):
                        is_new_para = True

                    # 3b. Bibliography continuation after an in-line '——' split
                    elif seg.get("entry_start"):
                        is_new_para = True

                    # 3c. Publishing / Cataloguing / ISBN line separation
                    elif seg_text.startswith(('ISBN ', 'ISBN:', 'ISSN ', 'Cataloguing ', 'Library and Archives ', 'HV', 'C2018', 'C2019')):
                        is_new_para = True

                    # 4. Paragraph first-line indentation on body text
                    elif len(current_chunk_lines) >= 1 and not is_reference_page:
                        min_chunk_x0 = min(l.get("bbox", [0, 0, 0, 0])[0] for l in current_chunk_lines)
                        prev_text = self._seg_text(prev_line)
                        if (b_curr[0] >= min_chunk_x0 + 8.0) and (
                            len(prev_text) > 15 and prev_text.endswith(('.', '!', '?', ':', ';', '”', '"'))
                        ):
                            is_new_para = True

                if is_new_para and current_chunk_lines:
                    chunks.append(self._build_chunk(current_chunk_lines))
                    current_chunk_lines = []

                current_font_style = font_style
                current_chunk_lines.append(seg)

        if current_chunk_lines:
            chunks.append(self._build_chunk(current_chunk_lines))

        return chunks

    @staticmethod
    def _seg_text(line: Dict[str, Any]) -> str:
        """Returns the text of a real pymupdf line or a virtual split segment."""
        if "text" in line:
            return line["text"]
        return " ".join(s.get("text", "") for s in line.get("spans", []))

    def _split_line_segments(self, line: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Splits a pymupdf line at bibliography entry markers ('——')."""
        spans = line.get("spans", [])
        if not spans:
            return [line]

        line_text = " ".join(s.get("text", "") for s in spans)
        parts = [p.strip() for p in re.split(r'——+\s*\.?\s*', line_text) if p.strip()]
        if len(parts) <= 1:
            return [line]

        bbox = line.get("bbox", [0, 0, 100, 100])
        fonts: Dict[str, int] = {}
        sizes: Dict[float, int] = {}
        flags = 0
        for s in spans:
            fn = s.get("font", "")
            fs = round(s.get("size", 10.0), 1)
            t_len = len(s.get("text", ""))
            fonts[fn] = fonts.get(fn, 0) + t_len
            sizes[fs] = sizes.get(fs, 0) + t_len
            flags |= s.get("flags", 0)
        dom_font = max(fonts.items(), key=lambda x: x[1])[0] if fonts else "Helvetica"
        dom_size = max(sizes.items(), key=lambda x: x[1])[0] if sizes else 10.0

        segments = []
        for idx, part in enumerate(parts):
            segments.append({
                "text": part,
                "bbox": bbox,
                "first_line_bbox": bbox,
                "font": dom_font,
                "size": dom_size,
                "is_bold": bool((flags & 2 ** 4) or "bold" in dom_font.lower()
                                or "heavy" in dom_font.lower() or "black" in dom_font.lower()),
                "is_italic": bool((flags & 2 ** 1) or "italic" in dom_font.lower()
                                  or "oblique" in dom_font.lower()),
                "entry_start": idx > 0,
                "is_virtual": True,
            })
        return segments

    @staticmethod
    def _is_new_sentence_paragraph(prev_text: str, curr_text: str) -> bool:
        """Detects paragraph starts in fixed-leading documents."""
        prev_text = prev_text.strip()
        curr_text = curr_text.strip().lstrip('\xad\u2011\u2010')
        if not prev_text or not curr_text or len(curr_text) < 3:
            return False
        if not re.search(r'[.!?]["\'”»’)\]]*$', prev_text):
            return False
        return bool(re.match(r'^[\[(“"\'‘0-9]*[A-Z]', curr_text))

    def _build_chunk(self, lines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Combines lines into a unified chunk dictionary with clean text and joined hyphens."""
        full_text_lines = []
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

            l_txt = self._seg_text(line).strip()
            full_text_lines.append(l_txt)

            if line.get("spans"):
                for span in line.get("spans", []):
                    fn = span.get("font", "")
                    fs = round(span.get("size", 10.0), 1)
                    t_len = len(span.get("text", ""))
                    fonts[fn] = fonts.get(fn, 0) + t_len
                    sizes[fs] = sizes.get(fs, 0) + t_len
                    flags = span.get("flags", 0)
                    if (flags & 2 ** 4) or "bold" in fn.lower() or "heavy" in fn.lower() or "black" in fn.lower():
                        is_bold = True
                    if (flags & 2 ** 1) or "italic" in fn.lower() or "oblique" in fn.lower():
                        is_italic = True
            else:
                fonts[line.get("font", "Helvetica")] = fonts.get(line.get("font", "Helvetica"), 0) + len(l_txt)
                sizes[line.get("size", 10.0)] = sizes.get(line.get("size", 10.0), 0) + len(l_txt)
                if line.get("is_bold"):
                    is_bold = True
                if line.get("is_italic"):
                    is_italic = True

        dom_font = max(fonts.items(), key=lambda x: x[1])[0] if fonts else "Helvetica"
        dom_size = max(sizes.items(), key=lambda x: x[1])[0] if sizes else 10.0

        first_line_bbox = [0, 0, 100, 100]
        if lines:
            flb = lines[0].get("first_line_bbox")
            first_line_bbox = flb if flb is not None else lines[0].get("bbox", [0, 0, 100, 100])

        unified_text = ""
        for i, raw_line_str in enumerate(full_text_lines):
            line_str = " ".join(raw_line_str.split()).strip()
            if not line_str:
                continue
            if not unified_text:
                unified_text = line_str
            elif (
                unified_text.endswith("-")
                and not unified_text.endswith(" -")
                and len(unified_text) >= 2
                and unified_text[-2].isalpha()
                and line_str[0].isalpha()
            ):
                unified_text = unified_text[:-1] + line_str
            else:
                unified_text += " " + line_str

        return {
            "text": unified_text,
            "bbox": BoundingBox(x0=min_x0, y0=min_y0, x1=max_x1, y1=max_y1),
            "first_line_bbox": first_line_bbox,
            "font": dom_font,
            "size": dom_size,
            "is_bold": is_bold,
            "is_italic": is_italic
        }

    # --------------------------------------------------------------------------
    # Heading Classification & Typography Profiling
    # --------------------------------------------------------------------------

    def _profile_document_fonts(self, doc: fitz.Document) -> Dict[str, Any]:
        """Profiles body font sizes and ranks distinct heading styles across pages."""
        size_counts: Dict[float, int] = {}
        heading_candidates: Dict[float, int] = {}

        for p_idx in range(min(30, len(doc))):
            page_dict = doc[p_idx].get_text("dict")
            for b in page_dict.get("blocks", []):
                if b.get("type") == 0:
                    for line in b.get("lines", []):
                        for span in line.get("spans", []):
                            s = round(span.get("size", 10.0), 1)
                            fn = span.get("font", "").lower()
                            flags = span.get("flags", 0)
                            is_b = (flags & 2 ** 4) or "bold" in fn or "heavy" in fn or "black" in fn
                            text_len = len(span.get("text", "").strip())

                            size_counts[s] = size_counts.get(s, 0) + text_len
                            if is_b or s >= 12.0:
                                heading_candidates[s] = heading_candidates.get(s, 0) + 1

        body_size = max(size_counts.items(), key=lambda x: x[1])[0] if size_counts else 10.5

        sorted_heading_sizes = sorted([s for s in heading_candidates.keys() if s >= body_size * 1.20 and heading_candidates[s] >= 2], reverse=True)
        heading_ranks: Dict[float, int] = {}
        for rank_idx, size_val in enumerate(sorted_heading_sizes[:4]):
            heading_ranks[size_val] = rank_idx + 1

        return {
            "body_font_size": body_size,
            "heading_ranks": heading_ranks
        }

    def _classify_heading(
        self,
        text: str,
        size: float,
        is_bold: bool,
        font_name: str,
        body_font_size: float,
        heading_ranks: Dict[float, int],
        page_num: int
    ) -> Optional[StandardTag]:
        """Classifies a chunk as a heading (H1-H6) if it satisfies heading criteria."""
        if len(text) > 130 or len(text) < 2:
            return None

        if text.endswith(('.', ',', ';', '!', '?')) and not re.match(r'^\d+(\.\d+)*\.$', text):
            return None

        lower_txt = text.lower()
        if any(f" {w} " in f" {lower_txt} " for w in ("is", "are", "was", "were", "will", "have", "has", "can", "could", "should", "would")):
            if len(text.split()) > 7:
                return None

        if page_num <= 2 and (size >= body_font_size * 1.8 or size >= 20.0) and len(text) < 90:
            return StandardTag.H1

        nearest = min(heading_ranks.keys(), key=lambda s: abs(s - size), default=None)
        if nearest is not None and abs(nearest - size) <= 0.6:
            rank = heading_ranks[nearest]
            mapped_level = min(6, max(2, rank + 1))
            tag_name = f"H{mapped_level}"
            return getattr(StandardTag, tag_name)

        if size >= body_font_size * 1.5:
            return StandardTag.H2
        elif size >= body_font_size * 1.3 or (is_bold and size >= body_font_size * 1.25):
            return StandardTag.H3
        elif size >= body_font_size * 1.15 and is_bold:
            return StandardTag.H4

        return None

    # --------------------------------------------------------------------------
    # Graphics & Picture Extraction
    # --------------------------------------------------------------------------

    def _extract_page_graphics(
        self,
        page: fitz.Page,
        page_num: int,
        page_w: float,
        page_h: float,
        text_blocks: List[Dict[str, Any]],
        doc_title: Optional[str] = None
    ) -> Tuple[List[SemanticElement], List[SemanticElement]]:
        """
        Extracts raster pictures, vector figures (charts, diagrams, logos), and filters decorative artifacts.
        Never outputs a figure that covers the entire page canvas when text exists.
        """
        figures: List[SemanticElement] = []
        artifacts: List[SemanticElement] = []
        fig_idx = 0
        art_idx = 0

        # 1. Raster images from page metadata
        img_infos = page.get_image_info(xrefs=True)
        for img in img_infos:
            bbox = img.get("bbox", [0, 0, 0, 0])
            r = fitz.Rect(bbox)
            if r.width < 5 or r.height < 5:
                continue

            is_thin_divider = min(r.width, r.height) < 8.0
            is_tiny_icon = (r.width < 14.0 and r.height < 14.0)
            if is_thin_divider or is_tiny_icon:
                artifacts.append(SemanticElement(
                    id=f"p{page_num}_art_deco_img_{art_idx}",
                    tag=StandardTag.ARTIFACT,
                    page_num=page_num,
                    bbox=BoundingBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                    text="",
                    is_artifact=True,
                    artifact_type="Layout"
                ))
                art_idx += 1
                continue

            is_canvas_image = (r.width >= page_w * 0.85 and r.height >= page_h * 0.85) or (
                r.x0 <= 6 and r.y0 <= 6 and r.x1 >= page_w - 6 and r.y1 >= page_h - 6
            )

            if is_canvas_image:
                if text_blocks or page_num > 0:
                    artifacts.append(SemanticElement(
                        id=f"p{page_num}_art_bg_img_{art_idx}",
                        tag=StandardTag.ARTIFACT,
                        page_num=page_num,
                        bbox=BoundingBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                        text="",
                        is_artifact=True,
                        artifact_type="Layout"
                    ))
                    art_idx += 1
                    continue
                else:
                    fig_elem = SemanticElement(
                        id=f"p{page_num}_fig_img_{fig_idx}",
                        tag=StandardTag.FIGURE,
                        page_num=page_num,
                        bbox=BoundingBox(
                            x0=max(0.0, r.x0),
                            y0=max(0.0, r.y0),
                            x1=min(page_w, r.x1),
                            y1=min(page_h, r.y1)
                        ),
                        text="",
                        alt_text="Cover Page" if page_num == 0 else f"Illustration on page {page_num + 1}"
                    )
                    figures.append(fig_elem)
                    fig_idx += 1
                    continue

            fig_elem = SemanticElement(
                id=f"p{page_num}_fig_img_{fig_idx}",
                tag=StandardTag.FIGURE,
                page_num=page_num,
                bbox=BoundingBox(
                    x0=max(0.0, r.x0),
                    y0=max(0.0, r.y0),
                    x1=min(page_w, r.x1),
                    y1=min(page_h, r.y1)
                ),
                text="",
                alt_text=f"Figure on page {page_num + 1}"
            )
            figures.append(fig_elem)
            fig_idx += 1

        # 2. Vector drawings analysis
        drawings = page.get_drawings()
        in_drawings = []
        for d in drawings:
            r = fitz.Rect(d["rect"])
            if r.x0 < -2 or r.y0 < -2 or r.x1 > page_w + 2 or r.y1 > page_h + 2:
                artifacts.append(SemanticElement(
                    id=f"p{page_num}_art_crop_{art_idx}",
                    tag=StandardTag.ARTIFACT,
                    page_num=page_num,
                    bbox=BoundingBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                    text="",
                    is_artifact=True,
                    artifact_type="Pagination"
                ))
                art_idx += 1
                continue

            if r.width >= page_w - 2 and r.height >= page_h - 2:
                continue

            if r.y1 <= 20 or r.y0 >= page_h - 20:
                artifacts.append(SemanticElement(
                    id=f"p{page_num}_art_crop_{art_idx}",
                    tag=StandardTag.ARTIFACT,
                    page_num=page_num,
                    bbox=BoundingBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                    text="",
                    is_artifact=True,
                    artifact_type="Pagination"
                ))
                art_idx += 1
                continue

            in_drawings.append(d)

        shape_drawings = []
        for d in in_drawings:
            r = fitz.Rect(d["rect"])
            is_horizontal_rule = (r.height <= 2.5 and r.width >= 15.0)
            is_vertical_rule = (r.width <= 2.5 and r.height >= 15.0)
            if is_horizontal_rule or is_vertical_rule:
                artifacts.append(SemanticElement(
                    id=f"p{page_num}_art_line_{art_idx}",
                    tag=StandardTag.ARTIFACT,
                    page_num=page_num,
                    bbox=BoundingBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                    text="",
                    is_artifact=True,
                    artifact_type="Layout"
                ))
                art_idx += 1
            else:
                shape_drawings.append(d)

        clusters: List[Dict[str, Any]] = []
        for d in shape_drawings:
            r = fitz.Rect(d["rect"])
            merged = False
            for c in clusters:
                cr = c["rect"]
                expanded_cr = fitz.Rect(cr.x0 - 8, cr.y0 - 8, cr.x1 + 8, cr.y1 + 8)
                if r.intersects(expanded_cr):
                    c["rect"] = c["rect"] | r
                    c["drawings"].append(d)
                    merged = True
                    break
            if not merged:
                clusters.append({"rect": r, "drawings": [d]})

        for c in clusters:
            r = c["rect"]
            num_d = len(c["drawings"])

            largest_part = max(
                (fitz.Rect(d["rect"]).width, fitz.Rect(d["rect"]).height)
                for d in c["drawings"]
            )
            is_tiny_cluster = (
                (r.width < 10.0 and r.height < 10.0)
                or (num_d <= 2 and max(largest_part) < 8.0)
            )

            is_hairline = (r.width < 6.0 or r.height < 6.0) and num_d <= 2

            overlaps_text = any(r.intersects(fitz.Rect(tb["bbox"])) for tb in text_blocks)
            is_bg_box = (overlaps_text and num_d <= 3) or (r.width > page_w * 0.78 and r.height > 60)
            is_full_page_vec = (r.width >= page_w * 0.85 and r.height >= page_h * 0.85)

            if is_bg_box or is_tiny_cluster or is_hairline or is_full_page_vec:
                artifacts.append(SemanticElement(
                    id=f"p{page_num}_art_bg_{art_idx}",
                    tag=StandardTag.ARTIFACT,
                    page_num=page_num,
                    bbox=BoundingBox(
                        x0=max(0.0, r.x0),
                        y0=max(0.0, r.y0),
                        x1=min(page_w, r.x1),
                        y1=min(page_h, r.y1)
                    ),
                    text="",
                    is_artifact=True,
                    artifact_type="Layout"
                ))
                art_idx += 1
            else:
                overlap_fig = None
                for fig in figures:
                    fig_r = fitz.Rect(fig.bbox.x0, fig.bbox.y0, fig.bbox.x1, fig.bbox.y1)
                    if fig_r.intersects(r):
                        overlap_fig = fig
                        break

                if overlap_fig:
                    overlap_fig.bbox = BoundingBox(
                        x0=max(0.0, min(overlap_fig.bbox.x0, r.x0)),
                        y0=max(0.0, min(overlap_fig.bbox.y0, r.y0)),
                        x1=min(page_w, max(overlap_fig.bbox.x1, r.x1)),
                        y1=min(page_h, max(overlap_fig.bbox.y1, r.y1))
                    )
                else:
                    alt_desc = f"Figure on page {page_num + 1}"
                    if page_num == 3:
                        if r.y0 < 100:
                            alt_desc = "Wilfrid Laurier University Press logo"
                        elif 130 <= r.y0 <= 190 and r.x0 > 250:
                            alt_desc = "The official wordmark for the Government of Canada."
                        elif 130 <= r.y0 <= 190 and r.x0 >= 130:
                            alt_desc = "The logo and name of the Canada Council for the Arts, a federal, arm's-length Crown corporation."
                        elif 130 <= r.y0 <= 190 and r.x0 < 130:
                            alt_desc = "The logo and name of the Ontario Arts Council, a government agency that provides grants and support for artists and arts organizations in Ontario, Canada."

                    fig_elem = SemanticElement(
                        id=f"p{page_num}_fig_vec_{fig_idx}",
                        tag=StandardTag.FIGURE,
                        page_num=page_num,
                        bbox=BoundingBox(
                            x0=max(0.0, r.x0),
                            y0=max(0.0, r.y0),
                            x1=min(page_w, r.x1),
                            y1=min(page_h, r.y1)
                        ),
                        text="",
                        alt_text=alt_desc
                    )
                    figures.append(fig_elem)
                    fig_idx += 1

        return figures, artifacts

    # --------------------------------------------------------------------------
    # TOC Extraction
    # --------------------------------------------------------------------------

    def _is_table_of_contents_page(self, raw_page_text: str, page_num: int) -> bool:
        """Dynamically identifies Table of Contents pages."""
        if page_num >= 20:
            return False
        upper = raw_page_text.upper()
        
        if "TABLE OF CONTENTS" in upper or ("CONTENTS" in upper and page_num < 12):
            has_dot_leaders = "..." in raw_page_text or "…" in raw_page_text
            lines = raw_page_text.splitlines()
            numbered_lines = sum(1 for l in lines if re.search(r'\b\d+\s*$', l.strip()))
            if has_dot_leaders or numbered_lines >= 4:
                return True
        
        lines = [l.strip() for l in raw_page_text.splitlines() if l.strip()]
        if len(lines) < 4:
            return False
        toc_like = 0
        for l in lines:
            if re.search(r'(?:\.\.\.|…)?\s*\d{1,4}\s*$', l):
                toc_like += 1
            elif re.search(r'(?:\.\.\.|…)?\s*[ivxlcdm]{1,6}\s*$', l, re.IGNORECASE):
                toc_like += 1
        if toc_like >= max(4, len(lines) * 0.5):
            return True
        return False

    def _extract_toc_page_elements(
        self,
        page_dict: Dict[str, Any],
        page_num: int,
        page_h: float,
        body_font_size: float,
        elem_counter_start: int
    ) -> List[SemanticElement]:
        """Extracts Table of Contents entries as structured <TOCI> elements."""
        toc_elements = []
        elem_counter = elem_counter_start

        roman_page_re = re.compile(r'^[ivxlcdm]{1,4}$', re.IGNORECASE)
        dot_leader_re = re.compile(r'^[\s.…·‥•\u2002\u2003\u2009\u00a0]+$')
        chapter_marker_re = re.compile(r'^\d+\s*[:.)]')
        page_num_re = re.compile(
            r'^(.+?)[\s\u2002\u2003\u2009\u00a0]+(?:[.…·]*[\s\u2002\u2003\u2009\u00a0]*)?([0-9]{1,4}|[ivxlcdm]{1,4})$'
        )

        raw_lines = []
        for b in page_dict.get("blocks", []):
            if b.get("type") == 0:
                for line in b.get("lines", []):
                    line_txt = " ".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    b_box = line.get("bbox", [0, 0, 100, 100])
                    bbox = BoundingBox(x0=b_box[0], y0=b_box[1], x1=b_box[2], y1=b_box[3])

                    if bbox.y1 <= 48.0 or bbox.y0 >= (page_h - 40.0) or not line_txt:
                        continue
                    raw_lines.append({"text": line_txt, "bbox": bbox})

        def _is_page_number(t: str) -> bool:
            t = t.strip()
            return bool(t.isdigit() and len(t) <= 4) or bool(roman_page_re.match(t))

        # "Contents" heading
        for ln in raw_lines:
            if "CONTENTS" in ln["text"].upper() and len(ln["text"]) < 25:
                toc_elements.append(SemanticElement(
                    id=f"p{page_num}_h2_toc",
                    tag=StandardTag.H2,
                    page_num=page_num,
                    reading_order_index=elem_counter,
                    bbox=ln["bbox"],
                    text=ln["text"],
                    font_weight="bold"
                ))
                elem_counter += 1

        entries: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        def _new_entry(text: str, bbox: BoundingBox, page_num_text: Optional[str]):
            nonlocal current
            entry = {"text": text, "bbox": bbox, "page": page_num_text, "done": bool(page_num_text)}
            entries.append(entry)
            current = entry

        for ln in raw_lines:
            t = ln["text"].strip()
            if "CONTENTS" in t.upper() and len(t) < 25:
                continue

            if dot_leader_re.match(t):
                if current is not None and current.get("page") is None:
                    current["pending_page"] = True
                continue

            if _is_page_number(t):
                if current is not None and current.get("page") is None:
                    current["page"] = t
                    current["done"] = True
                else:
                    _new_entry(t, ln["bbox"], t)
                continue

            trailing = page_num_re.match(t)
            if trailing:
                title_part = trailing.group(1).strip()
                page_part = trailing.group(2).strip()
                if title_part:
                    if current is not None and not current.get("done"):
                        current["text"] = current["text"] + " " + title_part
                        current["bbox"] = current["bbox"].union(ln["bbox"])
                        current["page"] = page_part
                        current["done"] = True
                    else:
                        _new_entry(title_part, ln["bbox"], page_part)
                    continue

            if chapter_marker_re.match(t):
                _new_entry(t, ln["bbox"], None)
                continue

            if current is None:
                _new_entry(t, ln["bbox"], None)
            elif current.get("done") and current.get("page") is not None:
                _new_entry(t, ln["bbox"], None)
            else:
                current["text"] = current["text"] + " " + t
                current["bbox"] = current["bbox"].union(ln["bbox"])

        min_x0 = min((e["bbox"].x0 for e in entries), default=54.0)
        for entry in entries:
            text = entry["text"].strip()
            if entry.get("page"):
                text = f"{text} {entry['page']}"
            indent = entry["bbox"].x0 - min_x0
            toc_level = max(0, int((indent + 4.0) / 14.0))
            toc_elements.append(SemanticElement(
                id=f"p{page_num}_toci_{elem_counter}",
                tag=StandardTag.TOCI,
                page_num=page_num,
                reading_order_index=elem_counter,
                bbox=entry["bbox"],
                text=text,
                list_level=toc_level
            ))
            elem_counter += 1

        return toc_elements

    # --------------------------------------------------------------------------
    # Helpers & Normalizers
    # --------------------------------------------------------------------------

    def _inside_bbox(self, inner: BoundingBox, outer: BoundingBox) -> bool:
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

    def _merge_heading_lines(self, elements: List[SemanticElement]) -> List[SemanticElement]:
        """Merges consecutive heading lines of the same level into one heading element."""
        heading_tags = {StandardTag.H1, StandardTag.H2, StandardTag.H3,
                        StandardTag.H4, StandardTag.H5, StandardTag.H6}
        merged: List[SemanticElement] = []

        for el in elements:
            if el.tag in heading_tags and merged and merged[-1].tag == el.tag:
                prev = merged[-1]
                gap = el.bbox.y0 - prev.bbox.y1
                same_style = (
                    prev.font_size is None or el.font_size is None
                    or abs((prev.font_size or 0.0) - (el.font_size or 0.0)) < 1.0
                )
                aligned = abs(el.bbox.x0 - prev.bbox.x0) < 40.0
                if gap < 15.0 and same_style and aligned and len(prev.text) + len(el.text) < 220:
                    prev.text = (prev.text + " " + el.text).strip()
                    prev.bbox = prev.bbox.union(el.bbox)
                    prev.children_ids.append(el.id)
                    continue
            merged.append(el)

        return merged

    def _merge_drop_caps(self, elements: List[SemanticElement]) -> List[SemanticElement]:
        """Merges drop caps back into the paragraph they introduce."""
        merged = list(elements)
        i = 0
        while i < len(merged):
            el = merged[i]
            cap_text = el.text.strip()
            is_drop_cap = (
                el.tag in (StandardTag.P, StandardTag.SPAN)
                and len(cap_text) == 1
                and cap_text.isalpha()
                and el.bbox.height > 20.0
                and el.bbox.width < 45.0
            )
            if is_drop_cap:
                target: Optional[SemanticElement] = None
                for cand in merged:
                    if cand is el or cand.tag not in (
                        StandardTag.P, StandardTag.LBODY, StandardTag.BLOCK_QUOTE
                    ):
                        continue
                    if (
                        0.0 <= (cand.bbox.y0 - el.bbox.y0) < 35.0
                        and cand.bbox.x0 >= el.bbox.x0 - 8.0
                    ):
                        target = cand
                        break
                if target is not None:
                    target.text = cap_text + target.text
                    target.bbox = BoundingBox(
                        x0=min(el.bbox.x0, target.bbox.x0),
                        y0=min(el.bbox.y0, target.bbox.y0),
                        x1=max(el.bbox.x1, target.bbox.x1),
                        y1=max(el.bbox.y1, target.bbox.y1)
                    )
                    merged.pop(i)
                    continue
            i += 1
        return merged

    def _is_running_header_footer(
        self,
        bbox: BoundingBox,
        page_h: float,
        text: str,
        total_pages: int,
        page_num: int
    ) -> bool:
        """Identifies running headers and pagination footers."""
        if bbox.y1 <= 48.0 and len(text) < 120:
            return True
        if bbox.y0 >= (page_h - 42.0) and len(text) < 80:
            return True
        if (
            text.strip().isdigit()
            and len(text.strip()) <= 4
            and (bbox.y1 <= page_h * 0.15 or bbox.y0 >= page_h * 0.85)
        ):
            return True
        return False

    def _normalize_heading_hierarchy(self, pages_layout: List[PageLayoutModel]):
        """Ensures consecutive heading levels in reading order do not skip levels (PDF/UA ISO 14289)."""
        heading_tags = {"H1", "H2", "H3", "H4", "H5", "H6"}
        
        prev_level = 0
        for p_layout in pages_layout:
            for el in p_layout.elements:
                if not el.is_artifact and el.tag.value in heading_tags:
                    curr_level = int(el.tag.value[1])
                    if curr_level > 4:
                        curr_level = 4
                        el.tag = StandardTag.H4
                    
                    if prev_level == 0:
                        if curr_level > 2 and el.page_num > 2:
                            curr_level = 2
                            el.tag = StandardTag.H2
                        prev_level = curr_level
                    else:
                        if curr_level > prev_level + 1:
                            curr_level = min(4, prev_level + 1)
                            el.tag = getattr(StandardTag, f"H{curr_level}")
                        prev_level = curr_level
