"""
Table Extractor & Matrix Grid Reconstructor
Detects bordered and unbordered tables, header cells (TH), data cells (TD),
colspans/rowspans, header scope attributes, and associates captions.

Table detection uses PyMuPDF's native table finder to locate candidate table
regions, then reconstructs the FULL row/column grid from the page text so that
unbordered (rule-less) tables and tables spanning many rows are captured with
complete cell coverage. Rotated tables (page /Rotate 90/270) are handled by
swapping the row/column axes in visual space.
"""

import re
from typing import List, Dict, Optional, Tuple

import pymupdf as fitz

from src.engine.models import TableModel, TableCellModel, BoundingBox

_CAPTION_RE = re.compile(
    r'^\s*(?:TABLE|Table|FIGURE|Figure|Fig\.|Exhibit|Box|Chart|Graph|Diagram|Photo|Illustration|Plate)'
    r'\s*[\d\.\:\-]*\s*[:\-–—]?\s*'
)

_NOTE_RE = re.compile(r'^\s*Note\s*[:.\-–—]\s*')


class TableExtractor:
    """
    Extracts tabular data structures and generates WCAG/PDF-UA compliant table models.
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def extract_all_tables(self, pdf_path: str) -> Dict[int, List[TableModel]]:
        """Extracts all tables across all pages of the document."""
        doc = fitz.open(pdf_path)
        tables_by_page: Dict[int, List[TableModel]] = {}
        for p_idx in range(len(doc)):
            page_tables = self.extract_tables_from_page(pdf_path, p_idx, doc[p_idx])
            if page_tables:
                tables_by_page[p_idx] = page_tables
        doc.close()
        return tables_by_page

    def extract_tables_from_page(
        self,
        pdf_path: str,
        page_num: int,
        fitz_page: fitz.Page,
    ) -> List[TableModel]:
        """
        Extracts all tables from the given page.

        Candidate table regions are discovered with PyMuPDF's line-based finder.
        For each candidate the full grid is reconstructed from the page text so
        rule-less table bodies get complete row/column coverage.
        """
        # Words must be captured BEFORE find_tables: the finder temporarily
        # mutates the page's rotation state, which corrupts later text output.
        words = self._page_words(fitz_page)
        if len(words) < 2:
            return []

        tables: List[TableModel] = []
        candidates: List[Tuple[float, float, float, float]] = []

        for strategy in ("lines", "text"):
            try:
                finder = fitz_page.find_tables(strategy=strategy)
                found = finder.tables if finder else []
            except Exception:
                found = []
            for tab in found:
                try:
                    bbox = tuple(float(v) for v in tab.bbox)
                except Exception:
                    continue
                if bbox in candidates:
                    continue
                try:
                    row_count = len(tab.extract())
                except Exception:
                    row_count = 1
                if strategy == "lines" or row_count >= 2:
                    candidates.append(bbox)
            if candidates:
                break

        seen: List[Tuple[float, float, float, float]] = []
        for bbox in candidates:
            if any(self._rects_overlap(bbox, s) for s in seen):
                continue
            seen.append(bbox)
            model = self._reconstruct_grid(fitz_page, words, bbox)
            if model is not None and model.rows_count >= 2 and model.cols_count >= 2:
                if self._is_data_table(model):
                    tables.append(model)

        return tables

    @staticmethod
    def _is_data_table(tbl: TableModel) -> bool:
        """Rejects text-region grids that merely look tabular.

        Genuine data tables are dense (most cells populated) and use short
        cell values. Prose pages reconstructed as grids produce sparse cells
        filled with long sentences, so they are filtered out here. The bounds
        are derived from the table's own contents rather than fixed offsets.
        """
        cells = tbl.cells
        if not cells:
            return False
        lens = [len(c.text) for c in cells if c.text]
        if not lens:
            return False
        lens.sort()
        median = lens[len(lens) // 2]
        fill = len(lens) / len(cells)
        return fill >= 0.6 and lens[-1] <= 8 * median

    @staticmethod
    def _page_words(fitz_page: fitz.Page) -> List[list]:
        """Extracts page words as [x0, y0, x1, y1, text, font_size] tuples."""
        words: List[list] = []
        try:
            d = fitz_page.get_text("dict")
            for b in d.get("blocks", []):
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        t = span.get("text", "").strip()
                        if not t:
                            continue
                        bbox = span.get("bbox")
                        if not bbox:
                            continue
                        words.append([bbox[0], bbox[1], bbox[2], bbox[3], t, span.get("size", 9.0)])
        except Exception:
            pass
        return words

    # ------------------------------------------------------------------ #
    # Grid reconstruction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rects_overlap(a: Tuple[float, float, float, float],
                       b: Tuple[float, float, float, float]) -> bool:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1

    def _reconstruct_grid(self, fitz_page: fitz.Page,
                          words: List[list],
                          region: Tuple[float, float, float, float]) -> Optional[TableModel]:
        """Rebuilds the full table grid from text words inside a candidate region."""
        rotation = fitz_page.rotation
        rotated = rotation in (90, 270)

        # find_tables reports regions in the page's display space; for rotated
        # pages convert back to the unrotated crop-relative space the text uses.
        if rotated:
            dm = fitz_page.derotation_matrix
            p0 = fitz.Point(region[0], region[1]) * dm
            p1 = fitz.Point(region[2], region[3]) * dm
            region = (min(p0.x, p1.x), min(p0.y, p1.y),
                      max(p0.x, p1.x), max(p0.y, p1.y))
        rx0, ry0, rx1, ry1 = region

        # Pages keep running headers/footers within the top/bottom margins;
        # those must never be absorbed into the table grid.
        crop = fitz_page.cropbox
        margin_lo = crop.y0 + 40.0
        margin_hi = crop.y1 - 32.0

        if rotated:
            row_key = lambda w: (w[0] + w[2]) / 2.0
            col_key = lambda w: (w[1] + w[3]) / 2.0
            lo_row, hi_row = rx0, rx1
        else:
            row_key = lambda w: (w[1] + w[3]) / 2.0
            col_key = lambda w: w[0]
            lo_row, hi_row = ry0, ry1

        def x_filter(w: list) -> bool:
            # Restricts the column axis to the candidate region for upright
            # pages; rotated pages are already bounded by the row band.
            if not rotated:
                return (rx0 - 12.0) <= w[0] and w[2] <= (rx1 + 12.0)
            return True

        def margin_ok(w: list) -> bool:
            yc = (w[1] + w[3]) / 2.0
            return margin_lo <= yc <= margin_hi

        # ----- seed words inside the region ------------------------------
        seed = [w for w in words
                if x_filter(w) and margin_ok(w)
                and (lo_row - 12.0) <= row_key(w) <= (hi_row + 40.0)]
        if len(seed) < 2:
            return None

        # All spacing tolerances derive from the median font size, which is
        # reliable even on rotated pages where span bbox heights are not.
        sizes = sorted(w[5] for w in seed)
        base_font = sizes[len(sizes) // 2] if sizes else 9.0
        font_floor = base_font * 0.8
        row_tol = max(4.0, base_font * 0.6)
        gap_tol = base_font * 2.5
        col_tol = max(12.0, base_font * 2.2)

        # Discard out-of-family text (captions, notes, footnotes rendered in a
        # smaller typeface) before clustering rows.
        seed = [w for w in seed if w[5] >= font_floor]
        if len(seed) < 2:
            return None

        row_centers = self._cluster(sorted(row_key(w) for w in seed), row_tol)
        if len(row_centers) < 2:
            return None

        # ----- extend rows in both directions over the page --------------
        row_centers = self._extend_rows(row_centers, words, row_key, gap_tol,
                                        hi_row, font_floor, margin_lo, margin_hi)
        if len(row_centers) < 2:
            return None

        # ----- full grid words -------------------------------------------
        r_min, r_max = min(row_centers), max(row_centers)
        grid_margin = max(row_tol, base_font)
        grid_words = [w for w in words
                      if x_filter(w) and margin_ok(w)
                      and (r_min - grid_margin) <= row_key(w) <= (r_max + grid_margin)]
        if len(grid_words) < 2:
            return None

        row_of: Dict[int, float] = {}
        for w in grid_words:
            rc = min(row_centers, key=lambda r: abs(r - row_key(w)))
            row_of[id(w)] = rc

        # ----- column clustering -----------------------------------------
        col_starts = self._cluster(sorted(col_key(w) for w in grid_words), col_tol)
        if len(col_starts) < 2:
            return None
        if rotated:
            # Rotated tables read right-to-left in the unrotated y axis; sort
            # columns by the visual left-to-right order to match the source.
            col_starts = col_starts[::-1]

        # ----- assign words to cells -------------------------------------
        cell_words: Dict[Tuple[float, float], List[list]] = {}
        for w in grid_words:
            rc = row_of[id(w)]
            cs = min(col_starts, key=lambda c: abs(col_key(w) - c))
            key = (round(rc, 1), round(cs, 1))
            cell_words.setdefault(key, []).append(w)

        rows = sorted({k[0] for k in cell_words})
        cols = sorted({k[1] for k in cell_words})
        if rotated:
            # Rotated tables read right-to-left in the unrotated y axis; keep
            # columns in visual left-to-right order to match the source.
            cols = cols[::-1]
        if len(rows) < 2 or len(cols) < 2:
            return None

        col_bounds = self._column_boundaries(col_starts, grid_words, col_key, rotated)

        # ----- merge stacked header rows -------------------------------
        rows = self._merge_header_rows(rows, cols, cell_words)

        # ----- build cell models ---------------------------------------
        cells: List[TableCellModel] = []
        for r in rows:
            row_index = rows.index(r)
            for c in cols:
                col_index = cols.index(c)
                ws = cell_words.get((r, c))
                if ws:
                    ws.sort(key=lambda w: (col_key(w), row_key(w)))
                    text = " ".join(w[4] for w in ws).strip()
                else:
                    text = ""
                bbox = self._cell_bbox(r, c, ws, col_bounds, rows, rotated)
                is_header = row_index == 0 and text != ""
                cells.append(TableCellModel(
                    row_index=row_index,
                    col_index=col_index,
                    row_span=1,
                    col_span=1,
                    is_header=is_header,
                    header_scope=None,
                    text=text,
                    bbox=bbox,
                ))

        if not cells:
            return None

        rows_count = max(c.row_index for c in cells) + 1
        cols_count = max(c.col_index for c in cells) + 1

        return TableModel(
            bbox=BoundingBox(x0=rx0, y0=ry0, x1=rx1, y1=ry1),
            rows_count=rows_count,
            cols_count=cols_count,
            cells=cells,
            has_headers=any(c.is_header for c in cells),
            summary=f"Table with {rows_count} rows and {cols_count} columns"
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _cluster(values: List[float], tol: float) -> List[float]:
        """Clusters sorted 1-D values into group centroids."""
        groups: List[List[float]] = []
        for v in values:
            if groups and v - groups[-1][-1] <= tol:
                groups[-1].append(v)
            else:
                groups.append([v])
        return [sum(g) / len(g) for g in groups]

    def _extend_rows(self, row_centers: List[float], words: List[list],
                     row_key, gap_tol: float, hi_row: float,
                     font_floor: float, margin_lo: float, margin_hi: float) -> List[float]:
        """Extends the row list upward and downward while rows stay contiguous.

        Candidate rows are rejected (and the walk stops) when they are too far
        away, fall outside the region's lower bound, are rendered in a smaller
        typeface (captions/notes/footnotes), live in the page margins, or read
        like a caption/note line.
        """
        if not row_centers:
            return row_centers
        result = list(row_centers)

        def row_members(r: float) -> List[list]:
            return [w for w in words if abs(row_key(w) - r) <= gap_tol / 2]

        def row_text(r: float) -> str:
            return " ".join(w[4] for w in row_members(r)).strip()

        def row_font(r: float) -> float:
            sizes = sorted(w[5] for w in row_members(r))
            return sizes[len(sizes) // 2] if sizes else 0.0

        def row_ok(r: float) -> bool:
            members = row_members(r)
            if not members:
                return False
            if not any(margin_lo <= (w[1] + w[3]) / 2.0 <= margin_hi for w in members):
                return False
            text = row_text(r)
            if _CAPTION_RE.match(text) or _NOTE_RE.match(text):
                return False
            return row_font(r) >= font_floor

        # Downward extension
        last = result[-1]
        candidates = sorted({round(row_key(w), 1) for w in words if row_key(w) > last + 1.0})
        for r in candidates:
            if r - last > gap_tol or r > hi_row + 60.0:
                break
            if not row_ok(r):
                break
            result.append(r)
            last = r

        # Upward extension (no hard bound; caption/font guards stop the walk)
        first = result[0]
        candidates = sorted({round(row_key(w), 1) for w in words if row_key(w) < first - 1.0},
                            reverse=True)
        for r in candidates:
            if first - r > gap_tol:
                break
            if not row_ok(r):
                break
            result.insert(0, r)
            first = r
        return result

    @staticmethod
    def _column_boundaries(col_starts: List[float], words: List[list],
                           col_key, rotated: bool) -> List[Tuple[float, float]]:
        """Computes [start, end) bounds for each column."""
        bounds = []
        for i, c in enumerate(col_starts):
            if i + 1 < len(col_starts):
                end = col_starts[i + 1]
            else:
                members = [w for w in words if abs(col_key(w) - c) <= 30.0]
                if members:
                    end = (max(w[3] for w in members) if rotated
                           else max(w[2] for w in members)) + 6.0
                else:
                    end = c + 60.0
            bounds.append((c, end))
        return bounds

    def _cell_bbox(self, row_c: float, col_c: float, ws: List[list],
                   col_bounds: List[Tuple[float, float]], rows: List[float],
                   rotated: bool) -> BoundingBox:
        # Row band from neighboring row centers.
        ridx = rows.index(row_c)
        ry0 = rows[ridx - 1] if ridx > 0 else row_c - 6.0
        ry1 = rows[ridx + 1] if ridx + 1 < len(rows) else row_c + 6.0
        lo = (ry0 + row_c) / 2.0
        hi = (ry1 + row_c) / 2.0
        cstart = cend = col_c
        for b in col_bounds:
            if abs(b[0] - col_c) < 0.01:
                cstart, cend = b
                break
        if ws:
            xs = [w[0] for w in ws] + [w[2] for w in ws]
            ys = [w[1] for w in ws] + [w[3] for w in ws]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        else:
            x0, y0, x1, y1 = cstart, lo, cend, hi
        if rotated:
            # Row axis is X in unrotated space; column axis is Y.
            return BoundingBox(x0=min(x0, lo), y0=min(y0, cstart), x1=max(x1, hi), y1=max(y1, cend))
        return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)

    @staticmethod
    def _merge_header_rows(rows: List[float], cols: List[float],
                           cell_words: Dict[Tuple[float, float], List[list]]) -> List[float]:
        """Merges stacked header rows at the top of the table into one row.

        The first row that is at least 85% populated is treated as the first
        full data row; every row above it is folded into the header row. Rows
        above the header that read like prose (captions split over two lines,
        notes, etc.) are dropped instead of merged.
        """
        if len(rows) < 2:
            return rows
        counts = {r: sum(1 for c in cols if cell_words.get((r, c))) for r in rows}
        max_count = max(counts.values())
        threshold = max(2, int(0.85 * max_count))
        dense = [r for r in rows if counts[r] >= threshold]
        if not dense:
            return rows
        first_dense = min(dense)
        header_rows = [r for r in rows if r < first_dense]
        if not header_rows:
            return rows
        kept = []
        for r in header_rows:
            ws = []
            for c in cols:
                ws.extend(cell_words.get((r, c), []))
            if len(ws) >= 4:
                numeric = sum(1 for w in ws if any(ch.isdigit() for ch in w[4]))
                if numeric <= 1:
                    continue
            kept.append(r)
        if not kept:
            return rows
        merged = kept[0]
        for r in kept[1:]:
            for c in cols:
                if cell_words.get((r, c)):
                    cell_words.setdefault((merged, c), []).extend(cell_words[(r, c)])
        return [merged] + [r for r in rows if r >= first_dense]