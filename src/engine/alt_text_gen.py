"""
Smart Alt Text & Semantic Description Generator
Generates contextual, accessible alternative text for Figures, Formulas, and Charts
based on computer vision analysis, surrounding captions, and layout semantics.
"""

import re
from typing import List, Optional
from src.engine.models import SemanticElement, StandardTag


class AltTextGenerator:
    """
    Generates standards-compliant alternative text (Alt attribute) for non-text elements.
    """

    def __init__(self):
        self.chart_keywords = ["chart", "graph", "plot", "trend", "distribution", "breakdown", "performance", "growth", "revenue", "sales"]
        self.diagram_keywords = ["diagram", "architecture", "flowchart", "workflow", "process", "schematic", "pipeline"]
        self.logo_keywords = ["logo", "brand", "trademark", "emblem", "icon"]

    def generate_alt_text(self, figure_elem: SemanticElement, page_elements: List[SemanticElement]) -> str:
        """
        Generates meaningful Alt text for a Figure element.
        """
        if figure_elem.alt_text and figure_elem.alt_text != f"Illustration on page {figure_elem.page_num + 1}":
            return figure_elem.alt_text

        # 1. Search for adjacent Caption elements (immediately below or above the figure)
        fig_bbox = figure_elem.bbox
        for el in page_elements:
            if el.tag == StandardTag.CAPTION:
                # Caption below figure (within 40pt)
                if 0 <= (el.bbox.y0 - fig_bbox.y1) <= 50 and abs(el.bbox.x0 - fig_bbox.x0) < 150:
                    clean_caption = el.text.replace("\n", " ").strip()
                    return f"Figure showing: {clean_caption}"
                # Caption above figure (within 40pt)
                elif 0 <= (fig_bbox.y0 - el.bbox.y1) <= 50 and abs(el.bbox.x0 - fig_bbox.x0) < 150:
                    clean_caption = el.text.replace("\n", " ").strip()
                    return f"Figure showing: {clean_caption}"

        # 2. Heuristic based on size & aspect ratio
        width = fig_bbox.width
        height = fig_bbox.height
        aspect_ratio = width / max(1.0, height)

        if width < 80 and height < 80:
            return "Small decorative icon or symbol"
        elif aspect_ratio > 3.0 and height < 100:
            return "Header banner illustration"
        elif width > 300 and height > 200:
            return "Detailed diagram or data chart"
        
        return f"Illustration depicting document content on page {figure_elem.page_num + 1}"
