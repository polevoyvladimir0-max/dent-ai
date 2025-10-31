import json
from pathlib import Path
from typing import Iterable, Dict, Any

INPUT = Path(r"C:\dent_ai\training\plans.jsonl")
OUTPUT_SFT = Path(r"C:\dent_ai\training\dataset_sft.jsonl")
OUTPUT_DPO = Path(r"C:\dent_ai\training\dataset_dpo.jsonl")

PROMPT_TEMPLATE = (
    "Доктор: {doctor}\nПациент: {patient}\nНомер карты: {card}\n"
    "Жалобы/анализ: {intake}\nКоды: {codes}\n"
    "Сформируй план лечения в формате маркдаун с разбивкой по этапам и рекомендациями."
)


def load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def dump_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_prompt(row: Dict[str, Any]) -> str:
    return PROMPT_TEMPLATE.format(
        doctor=row.get("doctor", ""),
        patient=row.get("patient", ""),
        card=row.get("card_number", ""),
        intake=row.get("intake", ""),
        codes=", ".join(row.get("codes", [])),
    )


def build_sft_rows():
    for row in load_jsonl(INPUT):
        yield {
            "prompt": build_prompt(row),
            "response": row.get("plan_text", ""),
            "metadata": {
                "plan_id": row.get("plan_id"),
                "created_at": row.get("created_at"),
            },
        }


def build_dpo_rows():
    for row in load_jsonl(INPUT):
        feedback = row.get("feedback") or []
        if not feedback:
            continue
        positive = next((fb for fb in feedback if fb.get("accepted")), None)
        negative = next((fb for fb in feedback if not fb.get("accepted")), None)
        if not positive or not negative:
            continue
        yield {
            "prompt": build_prompt(row),
            "chosen": positive.get("comments") or json.dumps(positive.get("diff"), ensure_ascii=False),
            "rejected": negative.get("comments") or json.dumps(negative.get("diff"), ensure_ascii=False),
            "metadata": {"plan_id": row.get("plan_id")},
        }


def main() -> None:
    dump_jsonl(OUTPUT_SFT, build_sft_rows())
    dump_jsonl(OUTPUT_DPO, build_dpo_rows())
    print(f"SFT saved to {OUTPUT_SFT}")
    print(f"DPO saved to {OUTPUT_DPO}")


if __name__ == "__main__":
    main()
