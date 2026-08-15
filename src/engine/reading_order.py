"""
Logical Reading Order Engine
Determines multi-column flows, separates sidebars, headers, and footers,
and produces a strictly ordered sequence of elements for accessible assistive reading.
"""

from typing import List, Dict, Tuple
from src.engine.models import SemanticElement, BoundingBox, StandardTag


class ReadingOrderEngine:
    """
    Computes human & screen-reader logical reading flow order across multi-column,
    sidebar, and mixed-layout documents.
    """

    def __init__(self, column_gap_threshold: float = 20.0):
        self.column_gap_threshold = column_gap_threshold

    def order_page_elements(self, elements: List[SemanticElement], page_width: float, page_height: float) -> List[SemanticElement]:
        """
        Orders page elements into logical reading sequence (topological column-aware sort).
        """
        if not elements:
            return []

        # 1. Filter out artifacts (headers, footers, page numbers) - they don't participate in main reading order
        body_elements = [el for el in elements if not el.is_artifact]
        artifact_elements = [el for el in elements if el.is_artifact]

        if not body_elements:
            return elements

        # 2. Detect column boundaries
        columns = self._detect_columns(body_elements, page_width)

        # 3. Assign elements to columns or full-width spans
        ordered_elements: List[SemanticElement] = []

        # Split elements into full-width (spanning across multiple columns, e.g. document title/banner)
        # and column-specific elements
        full_width_elements = []
        column_assigned_elements: Dict[int, List[SemanticElement]] = {i: [] for i in range(len(columns))}

        col_count = len(columns)
        if col_count > 1:
            for el in body_elements:
                # If element spans across more than 70% of page width, treat as full-width banner/header
                if el.bbox.width > (page_width * 0.7):
                    full_width_elements.append(el)
                else:
                    # Find which column element center falls into
                    center_x = (el.bbox.x0 + el.bbox.x1) / 2.0
                    assigned_col = 0
                    min_dist = float('inf')
                    for c_idx, (col_x0, col_x1) in enumerate(columns):
                        if col_x0 <= center_x <= col_x1:
                            assigned_col = c_idx
                            break
                        dist = min(abs(center_x - col_x0), abs(center_x - col_x1))
                        if dist < min_dist:
                            min_dist = dist
                            assigned_col = c_idx
                    column_assigned_elements[assigned_col].append(el)
        else:
            column_assigned_elements[0] = body_elements

        # 4. Sort full-width and column elements
        # For single-column / simple flow: sort primarily top-to-bottom (y0), then left-to-right (x0)
        if col_count <= 1:
            body_elements.sort(key=lambda el: (round(el.bbox.y0 / 5) * 5, el.bbox.x0))
            ordered_elements.extend(body_elements)
        else:
            # Multi-column flow:
            # 1. Top full-width elements (y0 < top of column blocks)
            # 2. Left column (top to bottom)
            # 3. Right column(s) (top to bottom)
            # 4. Bottom full-width elements (y0 > bottom of columns)
            full_width_elements.sort(key=lambda el: el.bbox.y0)
            
            # Sort each column top-to-bottom
            for c_idx in range(col_count):
                column_assigned_elements[c_idx].sort(key=lambda el: (round(el.bbox.y0 / 5) * 5, el.bbox.x0))

            # Merge based on vertical bands
            for el in full_width_elements:
                if el.bbox.y0 < page_height * 0.25:
                    ordered_elements.append(el)

            for c_idx in range(col_count):
                for el in column_assigned_elements[c_idx]:
                    if el not in ordered_elements:
                        ordered_elements.append(el)

            for el in full_width_elements:
                if el not in ordered_elements:
                    ordered_elements.append(el)

        # 5. Append artifacts at the end or preserve them
        ordered_elements.extend(artifact_elements)

        # Assign reading_order_index
        for idx, el in enumerate(ordered_elements):
            el.reading_order_index = idx

        return ordered_elements

    def _detect_columns(self, elements: List[SemanticElement], page_width: float) -> List[Tuple[float, float]]:
        """
        Detects 1, 2, or 3 column partitions across the page width.
        """
        if len(elements) < 4:
            return [(0.0, page_width)]

        # Sample x-intervals of text blocks
        x_centers = [(el.bbox.x0 + el.bbox.x1) / 2.0 for el in elements if el.bbox.width < (page_width * 0.65)]
        if not x_centers:
            return [(0.0, page_width)]

        mid_point = page_width / 2.0
        left_count = sum(1 for x in x_centers if x < mid_point - 30)
        right_count = sum(1 for x in x_centers if x > mid_point + 30)

        # If both halves have substantial distinct elements, it's 2-column
        if left_count >= 3 and right_count >= 3 and abs(left_count - right_count) < max(left_count, right_count) * 0.8:
            return [
                (0.0, mid_point),
                (mid_point, page_width)
            ]

        return [(0.0, page_width)]
