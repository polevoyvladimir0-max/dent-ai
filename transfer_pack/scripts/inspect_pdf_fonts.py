import fitz
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "storage"

if __name__ == "__main__":
    pdfs = sorted(PDF_DIR.glob("plan_*.pdf"), key=lambda p: p.stat().st_mtime)
    if not pdfs:
        raise SystemExit("PDF files not found")
    path = pdfs[-1]
    doc = fitz.open(path)
    fonts = set()
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fonts.add((span.get("font"), span.get("size"), span.get("flags")))
    print(path.name)
    for name, size, flags in sorted(fonts):
        print(name, size, flags)
