"""
Index Page Tag Fixer
--------------------
Post-processing module that fixes auto-tagged index pages where the
tagger's /P (paragraph) struct elements don't line up with the real
index entries. Uses hanging-indent detection from the page content
stream to re-split /P tags at true entry boundaries.

HOW IT DETECTS THE REAL ENTRY BOUNDARIES
-----------------------------------------
This index uses a hanging-indent style: the first line of every entry
starts flush at the column's left margin; wrapped continuation lines
are indented a few points further right. We read each MCID's
real (x, y) position from the page content stream. Any run of text
that (a) starts a new line AND (b) sits at the column margin is a new
entry. Everything else is a continuation of the previous entry.

WHAT IT EDITS
-------------
1. Flattens the existing /P elements for a page into one ordered list
   of children (plain MCID ints + nested /Link elements, untouched).
2. Re-cuts that list into new /P groups at the detected entry starts.
3. Replaces the old /P run in StructTreeRoot/K with the new /P elements.
4. Rebuilds the page's entry in /StructTreeRoot/ParentTree so every
   MCID still resolves to its correct (new) parent.
"""

import pikepdf
from pikepdf import Array, Dictionary, Name
from pikepdf import parse_content_stream
from typing import List, Optional

from src.engine.logger import logger


# ---------------------------------------------------------------------
# Step 1: read text + starting (x, y) for every MCID on a page
# ---------------------------------------------------------------------
def _get_mcid_text_and_pos(pdf, page_index):
    """
    Returns {mcid: {"text": str, "x": float, "y": float, "block": int}}.
    "block" is the index of the BT..ET block the text was drawn in --
    exported (e.g. InDesign) PDFs often split one visual column across
    several BT blocks, so coordinates should be compared within the
    same block before being compared across blocks.
    """
    page = pdf.pages[page_index]
    instructions = parse_content_stream(page)

    mcid_stack = []
    tm = [1, 0, 0, 1, 0, 0]
    bt_idx = -1
    out = {}

    def cur_mcid():
        for m in reversed(mcid_stack):
            if m is not None:
                return m
        return None

    def record(mcid, text, x, y, block):
        e = out.setdefault(mcid, {"text": "", "x": x, "y": y, "block": block})
        e["text"] += text

    for instr in instructions:
        op = str(instr.operator)
        ops = instr.operands

        if op == 'BT':
            bt_idx += 1
            tm = [1, 0, 0, 1, 0, 0]
        elif op in ('Td', 'TD'):
            tm[4] += float(ops[0])
            tm[5] += float(ops[1])
        elif op == 'Tm':
            tm = [float(v) for v in ops]
        elif op == 'BDC':
            props = ops[1] if len(ops) > 1 else None
            mcid = None
            if isinstance(props, pikepdf.Dictionary) and '/MCID' in props:
                mcid = int(props['/MCID'])
            mcid_stack.append(mcid)
        elif op == 'BMC':
            mcid_stack.append(None)
        elif op == 'EMC':
            if mcid_stack:
                mcid_stack.pop()
        elif op in ('Tj', "'", '"'):
            m = cur_mcid()
            if m is not None:
                record(m, str(ops[-1]), tm[4], tm[5], bt_idx)
        elif op == 'TJ':
            m = cur_mcid()
            if m is not None:
                s = ''.join(str(it) for it in ops[0]
                            if isinstance(it, (pikepdf.String, str)))
                record(m, s, tm[4], tm[5], bt_idx)

    return out


# ---------------------------------------------------------------------
# Step 2: struct-tree helpers
# ---------------------------------------------------------------------
def _first_mcid(el):
    """Find the first MCID referenced anywhere under a struct child."""
    if isinstance(el, int):
        return int(el)
    if isinstance(el, pikepdf.Dictionary):
        if '/MCID' in el:
            return int(el['/MCID'])
        if '/K' in el:
            return _first_mcid(el['/K'])
    if isinstance(el, pikepdf.Array):
        for c in el:
            m = _first_mcid(c)
            if m is not None:
                return m
    return None


def _all_mcids(el):
    found = []
    if isinstance(el, int):
        found.append(int(el))
    elif isinstance(el, pikepdf.Dictionary):
        if '/MCID' in el:
            found.append(int(el['/MCID']))
        elif '/K' in el:
            found.extend(_all_mcids(el['/K']))
    elif isinstance(el, pikepdf.Array):
        for c in el:
            found.extend(_all_mcids(c))
    return found


# ---------------------------------------------------------------------
# Step 3: fix one page
# ---------------------------------------------------------------------
def _fix_page(pdf, st, page_index, margin_tolerance=1.0):
    page = pdf.pages[page_index]
    info = _get_mcid_text_and_pos(pdf, page_index)
    if not info:
        logger.debug(f"  Index fix page {page_index+1}: no tagged text found, skipping", "INDEX_FIX")
        return

    kids = st.K
    page_positions = [i for i, el in enumerate(kids)
                       if isinstance(el, pikepdf.Dictionary)
                       and el.get('/Pg') == page.obj]
    if not page_positions:
        logger.debug(f"  Index fix page {page_index+1}: no struct elements found, skipping", "INDEX_FIX")
        return

    p_positions = [i for i in page_positions if str(kids[i].get('/S')) == '/P']
    if not p_positions:
        logger.debug(f"  Index fix page {page_index+1}: no /P elements found, skipping", "INDEX_FIX")
        return
    ps, pe = min(p_positions), max(p_positions)
    elems = [kids[i] for i in range(ps, pe + 1) if str(kids[i].get('/S')) == '/P']

    # Flatten children in original order, keeping the original objects
    atoms = []  # (child_object_or_int, representative_mcid)
    for p in elems:
        k = p.K
        children = k if isinstance(k, pikepdf.Array) else [k]
        for child in children:
            m = _first_mcid(child)
            if m is not None:
                atoms.append((child, m))

    if not atoms:
        return

    page_w = 612.0
    if '/MediaBox' in page:
        page_w = float(page.MediaBox[2])
    col_mid = page_w / 2.0

    # Margin computed PER (BT-block, column): index pages are almost always
    # two-column layout. A page-wide or single BT-block margin causes all
    # right-column entries to be falsely marked as non-margin continuation lines.
    block_col_margins = {}
    for _, m in atoms:
        if m in info:
            b = info[m]['block']
            x = info[m]['x']
            col = 0 if x < col_mid else 1
            key = (b, col)
            block_col_margins[key] = min(block_col_margins.get(key, x), x)

    entry_starts = []
    prev_y = None
    prev_block = None
    prev_col = None
    for _, m in atoms:
        if m not in info:
            entry_starts.append(False)
            continue
        x, y, b = info[m]['x'], info[m]['y'], info[m]['block']
        col = 0 if x < col_mid else 1
        col_margin = block_col_margins.get((b, col), x)
        new_line = prev_y is None or prev_block != b or prev_col != col or abs(y - prev_y) > 0.5
        at_margin = (x - col_margin) <= margin_tolerance
        entry_starts.append(new_line and at_margin)
        prev_y, prev_block, prev_col = y, b, col
    entry_starts[0] = True

    # Regroup into new entries
    groups = []
    cur = []
    for (child, _), is_start in zip(atoms, entry_starts):
        if is_start and cur:
            groups.append(cur)
            cur = []
        cur.append(child)
    if cur:
        groups.append(cur)

    # Only replace if new groups are formed that split merged elements further
    if len(groups) <= len(elems):
        logger.verbose(f"  Index fix page {page_index+1}: keeping existing {len(elems)} entries")
        return

    logger.verbose(f"  Index fix page {page_index+1}: {len(elems)} old /P tags -> {len(groups)} entries")

    parent_ref = elems[0].get('/P')
    new_elems = []
    for group in groups:
        newp = pdf.make_indirect(
            Dictionary(S=Name('/P'), P=parent_ref, Pg=page.obj, K=Array(group))
        )
        new_elems.append(newp)

    # Splice the new /P run into StructTreeRoot/K in place of the old one
    kids_list = list(kids)
    new_kids = kids_list[:ps] + new_elems + kids_list[pe + 1:]
    st.K = Array(new_kids)

    # Rebuild this page's slice of /ParentTree so every MCID points at
    # its correct new parent
    if '/ParentTree' not in st:
        nt = pikepdf.NumberTree.new(pdf)
        st['/ParentTree'] = pdf.make_indirect(nt.obj)
    else:
        nt = pikepdf.NumberTree(st.ParentTree)

    sp_obj = page.get('/StructParents')
    if sp_obj is None:
        sp_index = max(list(nt.keys()) + [-1]) + 1
        page['/StructParents'] = sp_index
    else:
        sp_index = int(sp_obj)

    max_mcid = max(info.keys())
    if sp_index in nt:
        parent_array = list(nt[sp_index])
        if len(parent_array) <= max_mcid:
            parent_array.extend([None] * (max_mcid + 1 - len(parent_array)))
    else:
        parent_array = [None] * (max_mcid + 1)

    for newp in new_elems:
        for child in newp.K:
            if isinstance(child, int):
                parent_array[int(child)] = newp
            else:
                for mm in _all_mcids(child):
                    if mm < len(parent_array):
                        parent_array[mm] = child

    nt[sp_index] = Array(parent_array)


def fix_index_pages(pdf_path: str, output_path: str, index_page_indices: List[int]):
    """
    Post-processes a tagged PDF to fix index page /P tag splitting.
    
    Args:
        pdf_path: Path to the tagged PDF
        output_path: Path to save the fixed PDF
        index_page_indices: List of 0-indexed page numbers that are index pages
    """
    if not index_page_indices:
        return

    pdf = pikepdf.open(pdf_path, allow_overwriting_input=True)
    if pdf.is_encrypted:
        try:
            pdf.authenticate("")
        except Exception:
            pass

    st = pdf.Root.get("/StructTreeRoot")
    if st is None:
        logger.debug("Index fix: No /StructTreeRoot found, skipping", "INDEX_FIX")
        pdf.close()
        return

    # Navigate to the document element's children
    root_k = st.get('/K')
    if isinstance(root_k, pikepdf.Dictionary):
        # root_k is the /Document element; its /K contains the actual children
        doc_elem = root_k
        if '/K' not in doc_elem:
            pdf.close()
            return
        st_for_fix = doc_elem
    else:
        st_for_fix = st

    logger.info(f"Phase 3b: Fixing index page tags for pages: {[p+1 for p in index_page_indices]}")
    for page_idx in index_page_indices:
        _fix_page(pdf, st_for_fix, page_idx)

    pdf.save(output_path)
    pdf.close()
    logger.success(f"Index page fix complete, saved to: {output_path}")
