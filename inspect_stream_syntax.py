import sys
import pikepdf
from pikepdf import Name, Dictionary, Array, Operator

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def inspect_stream_syntax(pdf_path: str, page_idx: int):
    print("=" * 60)
    print(f"STREAM SYNTAX: {pdf_path} (Page {page_idx+1})")
    print("=" * 60)
    
    pdf = pikepdf.open(pdf_path)
    page = pdf.pages[page_idx]
    ops = list(pikepdf.parse_content_stream(page))
    
    print(f"Total instructions: {len(ops)}")
    # Print first 25 text/marked content instructions
    count = 0
    for op in ops:
        if op.operator in (Operator('BDC'), Operator('BMC'), Operator('EMC'), Operator('BT'), Operator('ET'), Operator('Tj'), Operator('TJ'), Operator('Do')):
            print(f"  {op.operator}: {op.operands}")
            count += 1
            if count >= 30:
                break
    pdf.close()

if __name__ == "__main__":
    inspect_stream_syntax("completed.pdf", 1)  # Page 2
    print("\n")
    inspect_stream_syntax("completed.pdf", 4)  # Page 5
