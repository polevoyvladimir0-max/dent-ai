import re
import sys
import ctypes
import pandas as pd
from pathlib import Path

SOURCE_PATH = Path(r"C:\dent_ai\pricing_catalog.xlsx")
OUTPUT_CSV = Path(r"C:\dent_ai\staging_price_items.csv")

COLUMN_ALIASES = {
    "code_or_section": {"код", "code", "код услуги", "номер", "section_or_code"},
    "name": {"наименование", "описание", "name"},
    "price": {"цена, руб", "цена", "стоимость", "price"},
    "okp_code": {"код 2", "окп", "окпд2", "окпд", "окп код"},
}

ALPHANUM_CODE = re.compile(r"^[A-ZА-Я]{1,3}\d{2,}$", re.IGNORECASE)


def _normalize_column(column: str | int) -> str | None:
    normalized = str(column).strip().lower()
    for canonical, aliases in COLUMN_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def _load_dataframe() -> pd.DataFrame:
    raw_df = pd.read_excel(SOURCE_PATH, dtype=str).dropna(how="all")
    rename_map: dict[str, str] = {}
    for column in raw_df.columns:
        canonical = _normalize_column(column)
        if canonical:
            rename_map[column] = canonical

    if {"code_or_section", "name", "price"}.issubset(rename_map.values()):
        normalized = raw_df.rename(columns=rename_map)
        if "okp_code" not in normalized:
            normalized["okp_code"] = pd.NA
        return normalized[["code_or_section", "name", "price", "okp_code"]]

    legacy = pd.read_excel(
        SOURCE_PATH,
        header=None,
        names=["code_or_section", "name", "price"],
        dtype={0: str, 1: str}
    ).dropna(how="all")
    legacy["okp_code"] = pd.NA
    return legacy


def _looks_like_code(value: str) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    squashed = candidate.replace(" ", "").replace("-", "").replace("_", "")
    digits_only = squashed.replace(".", "")
    if digits_only.isdigit() and 4 <= len(digits_only) <= 8:
        return True
    return bool(ALPHANUM_CODE.match(candidate))


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

df = _load_dataframe()
df["code_or_section"] = df["code_or_section"].astype(str).str.strip()
df["name"] = df["name"].astype(str).str.strip()
df["okp_code"] = df["okp_code"].astype(str).str.strip().replace({"nan": pd.NA, "None": pd.NA})

section_mask = ~df["code_or_section"].apply(_looks_like_code)
df["section"] = df.loc[section_mask, "code_or_section"].replace({"nan": pd.NA})
df["section"] = df["section"].ffill()

items = (
    df.loc[~section_mask]
      .assign(
          code=lambda d: d["code_or_section"],
          display_name=lambda d: d["name"],
          base_price=lambda d: pd.to_numeric(d["price"], errors="coerce"),
          okp_code=lambda d: d["okp_code"].replace({pd.NA: None})
      )
      .drop(columns=["code_or_section", "name", "price"])
      .dropna(subset=["base_price"])
      .reset_index(drop=True)
)

print("Первые строки:")
print(items.head(10))
print(f"Всего услуг: {len(items)}; уникальных разделов: {items['section'].nunique()}")

items.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
print(f"Сохранено в {OUTPUT_CSV}")
