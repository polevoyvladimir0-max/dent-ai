from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from pdf_generator import generate_pdf

if __name__ == "__main__":
    plan = {
        "items": [
            {
                "code": "202209",
                "display_name": "Прием (осмотр, консультация) врача-стоматолога-хирурга повторный (амбулаторно)",
                "section": "Консультации",
                "base_price": 200,
                "count": 1,
                "sum": 200,
            },
            {
                "code": "809000",
                "display_name": "Наложение шва на слизистую оболочку рта",
                "section": "Хирургия",
                "base_price": 300,
                "count": 4,
                "sum": 1200,
            },
        ],
        "total": 1400,
    }
    path = generate_pdf(plan, "Грачёв", "Пациент", "234567")
    print(path)
