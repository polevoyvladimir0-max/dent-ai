import sys
import ctypes
import pandas as pd
from pathlib import Path

from scripts.search_price import match_guideline, load_guidelines

# Настраиваем вывод UTF-8 независимо от текущей кодировки консоли
try:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.SetConsoleOutputCP(65001)
    kernel32.SetConsoleCP(65001)
except Exception:
    pass

for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

CSV_PATH = Path(r"C:\dent_ai\staging_price_items.csv")
OUTPUT_PATH = Path(r"C:\dent_ai\plan_result.txt")

# При необходимости можно задать человеко-читаемые синонимы
CODE_ALIASES = {
    # "800202": "Ваше название позиции",
}

codes = [
    "202208",
    "800202",
    "800202",
    "809102",
    "809107",
    "809000",
    "809000",
    "809000",
    "800000",
]

items = pd.read_csv(CSV_PATH, dtype={"code": str})

rows = []
for code in codes:
    match = items.loc[items["code"] == code]
    if match.empty:
        raise SystemExit(f"код {code} не найден в прайсе")
    row = match.iloc[0].copy()
    row["display_name"] = CODE_ALIASES.get(code, row["display_name"])
    rows.append(row)

plan_df = pd.DataFrame(rows)
plan_df["count"] = 1
collapsed = (
    plan_df.groupby(["code", "display_name", "base_price"], as_index=False)["count"].sum()
)
collapsed["sum"] = collapsed["base_price"] * collapsed["count"]
collapsed["guideline"] = collapsed["code"].map(
    lambda c: (match_guideline(c) or {}).get("summary", "")
)

total_line = f"Итого: {collapsed['sum'].sum():.2f} руб."

print(collapsed.to_string(index=False))
print(total_line)

with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
    fh.write(collapsed.to_string(index=False))
    fh.write("\n" + total_line + "\n")

print(f"Результат сохранён в {OUTPUT_PATH}")
