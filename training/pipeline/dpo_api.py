import argparse
import json
from pathlib import Path

from openai import OpenAI

INPUT = Path(r"C:\dent_ai\training\dataset_dpo.jsonl")


def iter_pairs(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main():
    parser = argparse.ArgumentParser(description="DPO-like training via OpenAI Responses API")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top_p", type=float, default=0.95)
    args = parser.parse_args()

    client = OpenAI()

    for idx, sample in enumerate(iter_pairs(INPUT), start=1):
        prompt = sample["prompt"]
        chosen = sample["chosen"]
        rejected = sample["rejected"]
        metadata = sample.get("metadata", {})

        response = client.responses.create(
            model=args.model,
            input=[
                {
                    "role": "system",
                    "content": "Ты стоматологический ассистент. Выдай план лечения.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={
                "type": "instructor",
                "instruction": "Предпочтительная версия",
                "positive_example": chosen,
                "negative_example": rejected,
            },
            temperature=args.temperature,
            top_p=args.top_p,
            reasoning={"effort": "medium"},
        )

        print(f"#{idx} tokens: {response.usage.total_tokens} meta: {metadata}")


if __name__ == "__main__":
    main()
