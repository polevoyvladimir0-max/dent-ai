from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "storage"
OUT_PATH = ROOT / "storage" / "plan_text.txt"

if __name__ == "__main__":
    pdfs = sorted(PDF_DIR.glob("plan_*.pdf"), key=lambda p: p.stat().st_mtime)
    if not pdfs:
        raise SystemExit("PDF files not found")

    path = pdfs[-1]
    doc = fitz.open(path)
    print(path.name)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for page_index, page in enumerate(doc, start=1):
            fh.write(f"--- page {page_index} ---\n")
            fh.write(page.get_text("text"))
            fh.write("\n")
    print(f"saved -> {OUT_PATH}")
