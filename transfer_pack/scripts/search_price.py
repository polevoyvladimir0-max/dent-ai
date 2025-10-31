import argparse
import sys
import ctypes
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer

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
GUIDELINES_PATH = Path(r"C:\dent_ai\knowledge\guidelines.json")
COLLECTION = "price_items_v1"
MODEL_NAME = "cointegrated/rubert-tiny2"
DEFAULT_TOP_K = 5
_items_cache: Optional[pd.DataFrame] = None
_model_cache: Optional[SentenceTransformer] = None
_guidelines_cache: Optional[List[dict]] = None


def load_items() -> pd.DataFrame:
    global _items_cache
    if _items_cache is None:
        if not CSV_PATH.exists():
            raise FileNotFoundError(f"Не найден CSV: {CSV_PATH}")
        _items_cache = pd.read_csv(CSV_PATH, dtype={"code": str})
    return _items_cache


def load_model() -> SentenceTransformer:
    global _model_cache
    if _model_cache is None:
        _model_cache = SentenceTransformer(MODEL_NAME)
    return _model_cache


def load_guidelines() -> List[dict]:
    global _guidelines_cache
    if _guidelines_cache is None:
        if GUIDELINES_PATH.exists():
            _guidelines_cache = pd.read_json(GUIDELINES_PATH).to_dict(orient="records")
        else:
            _guidelines_cache = []
    return _guidelines_cache


def search_by_code(code: str) -> pd.DataFrame:
    items = load_items()
    match = items.loc[items["code"] == code]
    if match.empty:
        raise SystemExit(f"Код {code} не найден в прайсе")
    return match


def search_by_query(query: str, top_k: int = DEFAULT_TOP_K) -> List[models.ScoredPoint]:
    model = load_model()
    vector = model.encode(query)

    client = QdrantClient(host="127.0.0.1", port=6333)
    results = client.search(
        collection_name=COLLECTION,
        query_vector=vector,
        limit=top_k,
    )
    return results


def format_score(score: float) -> str:
    return f"{score:.3f}"


def handle_query(query: str, top_k: int) -> None:
    results = search_by_query(query, top_k)
    if not results:
        print("Ничего не найдено")
        return

    print(f"Запрос: {query}")
    print("Результаты:")
    for idx, point in enumerate(results, start=1):
        payload = point.payload or {}
        code = payload.get("code", "—")
        name = payload.get("display_name", "—")
        price = payload.get("base_price", "—")
        section = payload.get("section", "—")
        guideline = match_guideline(code)
        guideline_note = (
            f" | Рекомендация: {guideline['summary']}"
            if guideline
            else ""
        )
        print(
            f"{idx}. [{format_score(point.score)}] код {code} | {name} | {price} ₽ | {section}{guideline_note}"
        )


def handle_code(code: str) -> None:
    match = search_by_code(code)
    print(match[["code", "display_name", "base_price", "section"]].to_string(index=False))
    guideline = match_guideline(code)
    if guideline:
        print(f"Рекомендация: {guideline['summary']} (см. {guideline['reference']})")


def match_guideline(code: str) -> Optional[dict]:
    guidelines = load_guidelines()
    for entry in guidelines:
        if code in entry.get("codes", []):
            return entry
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Поиск по прайсу клиники")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", type=str, help="Текстовый запрос")
    group.add_argument("--code", type=str, help="Точный код услуги")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_K, help="Количество результатов")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.query:
        handle_query(args.query, args.top)
    elif args.code:
        handle_code(args.code)
    else:
        parser.error("Нужно указать --query или --code")


if __name__ == "__main__":
    main(sys.argv[1:])
