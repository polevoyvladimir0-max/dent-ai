"""
Быстрая инициализация нескольких шаблонов планов лечения для тестирования поиска.

Скрипт создаёт 3 эталонных шаблона (Straumann, двухэтапный пародонтология→протезирование,
и базовый синуслифт+имплантация), сохраняет их в SQLite и сразу пушит эмбеддинги в Qdrant.
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

# Гарантируем, что локальная SQLite используется, если DATABASE_URL не задан
os.environ.setdefault("SQLITE_PATH", str(BASE_DIR / "storage" / "state.db"))

from db import SessionLocal, Doctor, PlanTemplate  # noqa: E402
from scripts.search_plan_templates import (  # noqa: E402
    ensure_collection,
    save_template_embedding,
)


SAMPLE_PLANS: List[Dict[str, Any]] = [
    {
        "title": "Straumann одномоментная имплантация 1.4",
        "ideological_plan": (
            "Удаление зуба 1.4 с разъединением корней, одномоментная установка имплантата "
            "Straumann с формирователем, наложение коллагена и швов, контрольная КТ."
        ),
        "codes": ["202208", "808004", "809102", "809107", "808009", "809000", "800000"],
        "metadata": {
            "brands": ["Straumann"],
            "localization": "1.4",
            "style": "single_stage_implant",
        },
    },
    {
        "title": "Пародонтология → временное протезирование",
        "ideological_plan": (
            "Первая стадия — полная профессиональная гигиена, закрытый кюретаж, лазерная "
            "обработка пародонтальных карманов. Вторая стадия — временные мостовидные протезы "
            "с тотальной коррекцией окклюзии."
        ),
        "codes": ["201002", "201103", "201301", "510100", "510400"],
        "metadata": {
            "stages": [
                "periodontology_sanitation",
                "temporary_prosthetics",
            ],
        },
    },
    {
        "title": "Синуслифт + имплантация Astra Tech",
        "ideological_plan": (
            "Операция синуслифтинга с установкой мембраны и костнозамещающего материала, "
            "установка имплантата Astra Tech, формирователя и шовный материал."
        ),
        "codes": ["808201", "808202", "809101", "809106", "809000"],
        "metadata": {
            "brands": ["Astra Tech"],
            "style": "sinuslift_implant",
        },
    },
]


def ensure_doctor(session: SessionLocal) -> Doctor:
    doctor = session.query(Doctor).first()
    if doctor:
        return doctor

    doctor = Doctor(
        name="Seed Доктор",
        telegram_id="seed-doctor",
        specialization="стоматолог-хирург",
        experience_years=15,
    )
    session.add(doctor)
    session.commit()
    session.refresh(doctor)
    return doctor


def template_exists(session: SessionLocal, doctor_id: int, plan_text: str) -> bool:
    return (
        session.query(PlanTemplate)
        .filter(
            PlanTemplate.doctor_id == doctor_id,
            PlanTemplate.ideological_plan == plan_text,
        )
        .first()
        is not None
    )


def main():
    ensure_collection()
    session = SessionLocal()

    try:
        doctor = ensure_doctor(session)
        created = 0

        for plan in SAMPLE_PLANS:
            plan_text = plan["ideological_plan"]
            if template_exists(session, doctor.id, plan_text):
                print(f"↺ Пропустил «{plan['title']}» — уже существует")
                continue

            template = PlanTemplate(
                doctor_id=doctor.id,
                ideological_plan=plan_text,
                codes_sequence=plan["codes"],
                plan_metadata=plan.get("metadata", {}),
            )
            session.add(template)
            session.commit()
            session.refresh(template)

            save_template_embedding(
                template_id=template.id,
                ideological_plan=plan_text,
                doctor_id=doctor.id,
                codes_sequence=plan["codes"],
                metadata=plan.get("metadata"),
            )

            created += 1
            print(f"✓ Добавлен шаблон #{template.id}: {plan['title']}")

        if created == 0:
            print("Все целевые шаблоны уже есть, ничего не делал.")
        else:
            print(f"\nГотово: создано {created} шаблон(ов) для доктора ID={doctor.id}")

    finally:
        session.close()


if __name__ == "__main__":
    main()

