import sys
import ctypes
import pandas as pd
from pathlib import Path

SOURCE_PATH = Path(r"C:\dent_ai\pricing_catalog.xlsx")
OUTPUT_CSV = Path(r"C:\dent_ai\staging_price_items.csv")

try:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.SetConsoleOutputCP(65001)
    kernel32.SetConsoleCP(65001)
except Exception:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if not SOURCE_PATH.exists():
    raise FileNotFoundError(f"Не найден Excel: {SOURCE_PATH}")

df = pd.read_excel(
    SOURCE_PATH,
    header=None,
    names=["section_or_code", "name", "price"],
    dtype={0: str, 1: str}
).dropna(how="all")

df["section_or_code"] = df["section_or_code"].astype(str).str.strip()
df["name"] = df["name"].astype(str).str.strip()

def is_code(value: str) -> bool:
    return isinstance(value, str) and value.isdigit() and len(value) == 6

section_mask = ~df["section_or_code"].apply(is_code)
df["section"] = df.loc[section_mask, "section_or_code"].replace({"nan": pd.NA})
df["section"] = df["section"].ffill()

items = (
    df.loc[~section_mask]
      .assign(
          code=lambda d: d["section_or_code"],
          display_name=lambda d: d["name"],
          base_price=lambda d: pd.to_numeric(d["price"], errors="coerce")
      )
      .drop(columns=["section_or_code", "name", "price"])
      .dropna(subset=["base_price"])
      .reset_index(drop=True)
)

print("Первые строки:")
print(items.head(10))
print(f"Всего услуг: {len(items)}; уникальных разделов: {items['section'].nunique()}")

items.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
print(f"Сохранено в {OUTPUT_CSV}")
