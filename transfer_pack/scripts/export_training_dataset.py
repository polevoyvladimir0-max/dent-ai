import argparse
import json
from pathlib import Path
from typing import Dict, Any, List

from db import SessionLocal, TreatmentPlan, PlanFeedback
from db import Session as SessionModel  # noqa: N812

DEFAULT_OUTPUT = Path(r"C:\dent_ai\training\plans.jsonl")


def plan_to_text(plan: Dict[str, Any]) -> str:
    items: List[Dict[str, Any]] = plan.get("items", []) if isinstance(plan, dict) else []
    lines: List[str] = []
    for item in items:
        code = item.get("code", "")
        name = item.get("display_name", "")
        count = item.get("count", 1)
        item_sum = item.get("sum", 0)
        lines.append(f"{code} — {name} × {count} → {item_sum} ₽")
    total = plan.get("total", 0) if isinstance(plan, dict) else 0
    lines.append(f"Итого: {total} ₽")
    return "\n".join(lines)


def build_doctor_title(session: SessionModel) -> str:
    doctor = session.doctor
    if doctor is None:
        return ""
    prefs = doctor.preferences or {}
    parts = []
    if doctor.specialization:
        parts.append(f"врач {doctor.specialization}")
    category = prefs.get("category")
    if category and str(category).lower() != "нет":
        parts.append(f"{category} категории")
    degree = prefs.get("degree")
    if degree and str(degree).lower() != "нет":
        parts.append(str(degree))
    if doctor.experience_years:
        parts.append(f"стаж {doctor.experience_years:g} лет")
    header = ", ".join(parts) if parts else "врач"
    return f"{header} {doctor.name}".strip()


def export(output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with SessionLocal() as session, output.open("w", encoding="utf-8") as fh:
        plans: List[TreatmentPlan] = (
            session.query(TreatmentPlan)
            .join(SessionModel)
            .filter(TreatmentPlan.status == "confirmed")
            .order_by(TreatmentPlan.created_at.desc())
            .all()
        )
        for plan in plans:
            sess = plan.session
            if sess is None:
                continue
            patient = sess.patient
            doctor_title = build_doctor_title(sess)
            feedback_rows: List[PlanFeedback] = (
                session.query(PlanFeedback)
                .filter(PlanFeedback.plan_id == plan.id)
                .order_by(PlanFeedback.created_at.asc())
                .all()
            )
            payload = {
                "plan_id": plan.id,
                "doctor": doctor_title,
                "doctor_raw": sess.doctor.name if sess.doctor else "",
                "patient": patient.name if patient else "",
                "card_number": patient.card_number if patient else "",
                "intake": sess.transcript or "",
                "codes": (sess.codes or "").split(),
                "plan_json": plan.plan_json,
                "plan_text": plan_to_text(plan.plan_json or {}),
                "feedback": [
                    {
                        "rating": fb.rating,
                        "accepted": fb.accepted,
                        "comments": fb.comments,
                        "diff": fb.diff_json,
                        "created_at": fb.created_at.isoformat() if fb.created_at else None,
                    }
                    for fb in feedback_rows
                ],
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
            }
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Экспорт подтверждённых планов в датасет")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Путь до JSONL файла")
    args = parser.parse_args()
    total = export(args.output)
    print(f"Экспортировано {total} планов в {args.output}")


if __name__ == "__main__":
    main()
