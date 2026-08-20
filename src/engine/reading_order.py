"""
Logical Reading Order Engine (Recursive XY-Cut++ & Semantic Flow Ordering)
Determines multi-column flows, horizontal layout bands, sidebars, headers,
captions, and lists to produce a strictly ordered sequence of elements for
accessible assistive reading (PDF/UA-1 ISO 14289-1 & WCAG 2.1 SC 1.3.2 Meaningful Sequence).
"""

from typing import List, Optional
import re
from src.engine.models import SemanticElement, BoundingBox, StandardTag


class ReadingOrderEngine:
    """
    Computes human & screen-reader logical reading flow order across multi-column,
    sidebar, multi-band, and mixed-layout documents using Recursive XY-Cut++
    and semantic element association.
    """

    def __init__(
        self,
        min_horizontal_gap: float = 18.0,
        min_vertical_gap: float = 8.0,
        line_snap_tolerance: float = 3.5
    ):
        self.min_horizontal_gap = min_horizontal_gap
        self.min_vertical_gap = min_vertical_gap
        self.line_snap_tolerance = line_snap_tolerance
        self.caption_pattern = re.compile(
            r'^(?:Figure|Fig\.|Table|Tbl\.|Illustration|Photo|Exhibit|Chart|Graph|Diagram|Box|Plate)\s*[\d\.\:\-]+',
            re.IGNORECASE
        )

    def order_page_elements(
        self,
        elements: List[SemanticElement],
        page_width: float,
        page_height: float
    ) -> List[SemanticElement]:
        """
        Orders page elements into a strict, logical reading sequence using XY-Cut++
        decomposition followed by semantic binding (captions, lists, headings).
        """
        if not elements:
            return []

        # 1. Separate artifacts (headers, footers, page numbers, decorative lines)
        body_elements = [el for el in elements if not el.is_artifact]
        artifact_elements = [el for el in elements if el.is_artifact]

        if not body_elements:
            for idx, el in enumerate(elements):
                el.reading_order_index = idx
            return elements

        # Check if page is predominantly TOC items (<TOCI>)
        toci_count = sum(1 for el in body_elements if el.tag == StandardTag.TOCI)
        if toci_count >= len(body_elements) * 0.5:
            # Sort TOC elements strictly top-to-bottom by y0, then x0
            ordered_body = sorted(body_elements, key=lambda el: (round(el.bbox.y0 / 3.0) * 3.0, el.bbox.x0))
        else:
            # 2. Segment page into horizontal bands (spanning elements and multi-column regions)
            ordered_body = self._order_page_bands(body_elements, page_width, page_height)

        # 3. Perform semantic binding post-processing:
        #    - Associate Captions with Figures/Tables
        #    - Keep List Labels (<Lbl>) paired with List Bodies (<LBody>)
        #    - Ensure Headings (<H1>-<H6>) lead their respective sections
        ordered_body = self._post_process_semantic_order(ordered_body, page_width, page_height)

        # 4. Attach artifacts at the very end (assistive technology skips /Artifact)
        artifact_elements.sort(key=lambda el: (el.bbox.y0, el.bbox.x0))
        final_elements = ordered_body + artifact_elements

        # 5. Assign sequential reading_order_index
        for idx, el in enumerate(final_elements):
            el.reading_order_index = idx

        return final_elements

    # --------------------------------------------------------------------------
    # Band & Column Ordering (XY-Cut++)
    # --------------------------------------------------------------------------

    def _order_page_bands(
        self,
        elements: List[SemanticElement],
        page_width: float,
        page_height: float
    ) -> List[SemanticElement]:
        """
        Partitions elements into vertical bands separated by spanning elements
        (e.g. Top Banner, Multi-column text, Full-width Table/Figure, Bottom Conclusion).
        """
        if len(elements) <= 1:
            return list(elements)

        min_x0 = min(el.bbox.x0 for el in elements)
        max_x1 = max(el.bbox.x1 for el in elements)
        content_width = max(1.0, max_x1 - min_x0)

        # Identify spanning elements (wide elements crossing multiple columns)
        spanning_elements: List[SemanticElement] = []
        non_spanning_elements: List[SemanticElement] = []

        for el in elements:
            # An element spans if it occupies >= 75% of content width or >= 70% of page width
            # and is wider than 220pt
            is_wide = (
                (el.bbox.width >= content_width * 0.75 or el.bbox.width >= page_width * 0.70) and
                el.bbox.width >= 220.0
            )
            # Spanning elements are typically titles, section headers, wide figures/tables, or full-width paragraphs
            if is_wide and el.tag in (
                StandardTag.H1, StandardTag.H2, StandardTag.H3,
                StandardTag.P, StandardTag.TABLE, StandardTag.FIGURE,
                StandardTag.SECT, StandardTag.DIV, StandardTag.BLOCK_QUOTE
            ):
                spanning_elements.append(el)
            else:
                non_spanning_elements.append(el)

        if not spanning_elements:
            return self._order_column_band(elements, page_width)

        # Sort spanning elements top-to-bottom
        spanning_elements.sort(key=lambda el: el.bbox.y0)

        ordered: List[SemanticElement] = []
        remaining_non_spanning = list(non_spanning_elements)

        for span_el in spanning_elements:
            # Collect non-spanning elements that START above this spanning
            # element. Tall elements (e.g. side figures) may extend into the
            # spanning band but still begin above it and must be read first.
            above_elements = [
                el for el in remaining_non_spanning
                if el.bbox.y0 <= span_el.bbox.y0 + 5.0
            ]
            if above_elements:
                ordered.extend(self._order_column_band(above_elements, page_width))
                for el in above_elements:
                    remaining_non_spanning.remove(el)

            ordered.append(span_el)

        # Process any remaining non-spanning elements below the last spanning element
        if remaining_non_spanning:
            ordered.extend(self._order_column_band(remaining_non_spanning, page_width))

        return ordered

    def _order_column_band(
        self,
        elements: List[SemanticElement],
        page_width: float
    ) -> List[SemanticElement]:
        """
        Orders a band of elements by identifying vertical columns and sorting each column.
        """
        if len(elements) <= 1:
            return list(elements)

        # Detect columns via horizontal gap projection and alignment clustering
        cols = self._detect_columns(elements, page_width)
        if len(cols) > 1:
            ordered: List[SemanticElement] = []
            for col_elems in cols:
                ordered.extend(self._sort_atomic_block(col_elems))
            return ordered

        return self._sort_atomic_block(elements)

    def _detect_columns(
        self,
        elements: List[SemanticElement],
        page_width: float
    ) -> List[List[SemanticElement]]:
        """
        Groups elements into distinct vertical reading columns based on x0/x1 clustering
        and horizontal gap gutters.
        """
        if len(elements) <= 1:
            return [elements]

        # Calculate bounding boxes
        sorted_by_x = sorted(elements, key=lambda el: el.bbox.x0)
        
        # Check for clear column gutters
        # 1. Cluster elements by left-edge alignment
        col_clusters: List[List[SemanticElement]] = []
        
        for el in sorted_by_x:
            matched_cluster = None
            for cluster in col_clusters:
                c_min_x0 = min(e.bbox.x0 for e in cluster)
                c_max_x1 = max(e.bbox.x1 for e in cluster)
                c_avg_x0 = sum(e.bbox.x0 for e in cluster) / len(cluster)
                
                # If left edge aligns closely (within 30pt) and doesn't conflict
                if abs(el.bbox.x0 - c_min_x0) <= 30.0 or abs(el.bbox.x0 - c_avg_x0) <= 25.0:
                    matched_cluster = cluster
                    break
                # Or if horizontal overlap between this element and the cluster is large (> 50%)
                h_overlap = max(0.0, min(el.bbox.x1, c_max_x1) - max(el.bbox.x0, c_min_x0))
                if h_overlap > min(el.bbox.width, c_max_x1 - c_min_x0) * 0.5:
                    matched_cluster = cluster
                    break

            if matched_cluster is not None:
                matched_cluster.append(el)
            else:
                col_clusters.append([el])

        # If only 1 cluster, return single column
        if len(col_clusters) <= 1:
            return [elements]

        # Sort clusters left to right by their minimum x0
        col_clusters.sort(key=lambda c: min(e.bbox.x0 for e in c))

        # Verify that clusters actually represent distinct columns. True columns
        # are separated by a real gutter (little horizontal overlap) AND overlap
        # vertically. Centered or full-width figures/captions between text blocks
        # overlap the text column horizontally and belong to the main flow, NOT a
        # side column. Clusters that share no vertical space (e.g. a bottom-left
        # block and a top-right block) are sequential bands, not columns.
        distinct_cols: List[List[SemanticElement]] = [col_clusters[0]]
        for i in range(1, len(col_clusters)):
            prev_col = distinct_cols[-1]
            curr_col = col_clusters[i]
            prev_x0 = min(e.bbox.x0 for e in prev_col)
            prev_x1 = max(e.bbox.x1 for e in prev_col)
            curr_x0 = min(e.bbox.x0 for e in curr_col)
            curr_x1 = max(e.bbox.x1 for e in curr_col)
            prev_y0 = min(e.bbox.y0 for e in prev_col)
            prev_y1 = max(e.bbox.y1 for e in prev_col)
            curr_y0 = min(e.bbox.y0 for e in curr_col)
            curr_y1 = max(e.bbox.y1 for e in curr_col)

            h_overlap = min(prev_x1, curr_x1) - max(prev_x0, curr_x0)
            v_overlap = min(prev_y1, curr_y1) - max(prev_y0, curr_y0)
            # Distinct columns require a real gutter: no more than ~25pt of
            # horizontal overlap AND shared vertical extent (true side-by-side
            # columns). Otherwise the elements are sequential flow.
            if h_overlap <= 25.0 and v_overlap > 0.0:
                distinct_cols.append(curr_col)
            else:
                # Merge into previous column
                prev_col.extend(curr_col)

        return distinct_cols

    def _sort_atomic_block(self, elements: List[SemanticElement]) -> List[SemanticElement]:
        """
        Sorts an atomic column or block top-to-bottom by y0, then left-to-right by x0.
        """
        def sort_key(el: SemanticElement):
            snapped_y = round(el.bbox.y0 / self.line_snap_tolerance) * self.line_snap_tolerance
            return (snapped_y, el.bbox.x0)

        return sorted(elements, key=sort_key)

    # --------------------------------------------------------------------------
    # Semantic Post-Processing & Binding
    # --------------------------------------------------------------------------

    def _post_process_semantic_order(
        self,
        elements: List[SemanticElement],
        page_width: float,
        page_height: float
    ) -> List[SemanticElement]:
        """
        Fine-tunes the ordered sequence:
        1. Captions paired adjacent to their target Figure or Table.
        2. List items (<Lbl> and <LBody>) kept strictly contiguous.
        3. Heading continuity (headings preceding their section text).
        """
        if len(elements) <= 1:
            return elements

        result = list(elements)
        result = self._bind_captions_to_media(result)
        result = self._bind_list_items(result)
        result = self._bind_headings_to_sections(result)

        return result

    def _bind_captions_to_media(self, elements: List[SemanticElement]) -> List[SemanticElement]:
        """
        Ensures figure/table captions are positioned immediately adjacent
        to their corresponding Figure or Table in the reading sequence.

        Caption-media pairing uses the true 2D distance between rectangles so that
        rotated/side captions (whose bounding boxes are tall, narrow strips) bind to
        the correct media instead of the first element sorted by Y.
        """
        captions: List[SemanticElement] = []
        media: List[SemanticElement] = []

        for el in elements:
            if el.tag == StandardTag.CAPTION or self.caption_pattern.search(el.text.strip()):
                captions.append(el)
            elif el.tag in (StandardTag.FIGURE, StandardTag.TABLE):
                media.append(el)

        if not captions or not media:
            return elements

        reordered = list(elements)
        for cap in captions:
            best_target: Optional[SemanticElement] = None
            best_dist = float('inf')
            for m in media:
                dist = self._rect_distance(cap.bbox, m.bbox)
                # Captions must be near their media; anything farther than a generous
                # band is a separate element and is left in place.
                if dist < best_dist and dist < 140.0:
                    best_dist = dist
                    best_target = m

            if best_target is None or best_target not in reordered or cap not in reordered:
                continue

            # Already correctly adjacent to its media -> leave the reading flow
            # untouched (a caption above the media must precede it; a caption
            # below must follow it).
            cap_idx = reordered.index(cap)
            target_idx = reordered.index(best_target)
            if cap.bbox.y1 <= best_target.bbox.y0 and cap_idx == target_idx - 1:
                continue
            if cap.bbox.y0 >= best_target.bbox.y1 and cap_idx == target_idx + 1:
                continue

            reordered.remove(cap)
            target_idx = reordered.index(best_target)
            # Placement: above -> before, below or to the right -> after.
            if cap.bbox.y1 <= best_target.bbox.y0:
                reordered.insert(target_idx, cap)
            else:
                reordered.insert(target_idx + 1, cap)

        return reordered

    @staticmethod
    def _rect_distance(a: BoundingBox, b: BoundingBox) -> float:
        """Euclidean distance between two axis-aligned rectangles (0 when they touch/overlap)."""
        dx = max(0.0, a.x0 - b.x1, b.x0 - a.x1)
        dy = max(0.0, a.y0 - b.y1, b.y0 - a.y1)
        return (dx * dx + dy * dy) ** 0.5

    def _bind_list_items(self, elements: List[SemanticElement]) -> List[SemanticElement]:
        """
        Ensures that <Lbl> is immediately followed by its matching <LBody>.
        """
        reordered = list(elements)
        i = 0
        while i < len(reordered) - 1:
            curr = reordered[i]
            if curr.tag == StandardTag.LBL:
                best_lbody_idx = None
                for j in range(i + 1, min(i + 4, len(reordered))):
                    candidate = reordered[j]
                    if candidate.tag == StandardTag.LBODY:
                        if abs(candidate.bbox.y0 - curr.bbox.y0) < 30.0:
                            best_lbody_idx = j
                            break
                if best_lbody_idx and best_lbody_idx != i + 1:
                    lbody = reordered.pop(best_lbody_idx)
                    reordered.insert(i + 1, lbody)
            i += 1
        return reordered

    def _bind_headings_to_sections(self, elements: List[SemanticElement]) -> List[SemanticElement]:
        """
        Ensures headings (<H1>-<H6>) precede the section content they introduce.

        Only elements that sit strictly above the heading AND share its column
        (x-alignment) are pulled before it; elements from a neighbouring column
        are left untouched so multi-column flow is preserved.
        """
        heading_tags = {StandardTag.H1, StandardTag.H2, StandardTag.H3, StandardTag.H4, StandardTag.H5, StandardTag.H6}
        reordered = list(elements)

        i = 0
        while i < len(reordered) - 1:
            curr = reordered[i]
            if curr.tag in heading_tags:
                j = i + 1
                while j < len(reordered):
                    other = reordered[j]
                    if other is curr:
                        # After moving a misplaced element the heading itself
                        # shifts right; skip past it and keep scanning.
                        j += 1
                        continue
                    if other.tag in heading_tags or other.is_artifact:
                        break
                    # Stop at the first element that is not misplaced: only an
                    # element placed AFTER the heading while being strictly
                    # ABOVE it in the same column is moved.
                    if (other.bbox.y1 >= curr.bbox.y0 - 2.0
                            or abs(other.bbox.x0 - curr.bbox.x0) >= 60.0):
                        break
                    reordered.pop(j)
                    reordered.insert(i, other)
                    i += 1
                    continue
            i += 1
        return reordered
