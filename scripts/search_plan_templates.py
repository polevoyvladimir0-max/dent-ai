"""
Утилита для работы с шаблонами планов лечения.
Включает сохранение в Qdrant и векторный поиск похожих планов.
"""

import os
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.http import models

from scripts.search_price import load_model, MODEL_NAME

# Коллекция в Qdrant для шаблонов планов
PLAN_TEMPLATES_COLLECTION = "plan_templates_v1"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Кеш для Qdrant клиента
_qdrant_client_cache: Optional[QdrantClient] = None


def get_qdrant_client() -> QdrantClient:
    """Получить клиент Qdrant (с кешированием)."""
    global _qdrant_client_cache
    if _qdrant_client_cache is None:
        qdrant_url = os.getenv("QDRANT_URL")
        if qdrant_url:
            _qdrant_client_cache = QdrantClient(url=qdrant_url)
        else:
            host = os.getenv("QDRANT_HOST", "127.0.0.1")
            port = int(os.getenv("QDRANT_PORT", "6333"))
            _qdrant_client_cache = QdrantClient(host=host, port=port)
    return _qdrant_client_cache


def ensure_collection() -> None:
    """Создать коллекцию для шаблонов планов, если её нет."""
    client = get_qdrant_client()
    
    if not client.collection_exists(PLAN_TEMPLATES_COLLECTION):
        model = load_model()
        embedding_size = model.get_sentence_embedding_dimension()
        
        logger.info(f"Creating collection {PLAN_TEMPLATES_COLLECTION} with embedding size {embedding_size}")
        client.create_collection(
            collection_name=PLAN_TEMPLATES_COLLECTION,
            vectors_config=models.VectorParams(
                size=embedding_size,
                distance=models.Distance.COSINE,
            ),
        )


def save_template_embedding(
    template_id: int,
    ideological_plan: str,
    doctor_id: int,
    codes_sequence: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Сохранить шаблон плана в Qdrant для векторного поиска.
    
    Args:
        template_id: ID шаблона из БД
        ideological_plan: Идеологический план (текст диктовки)
        doctor_id: ID врача
        codes_sequence: Последовательность кодов
        metadata: Дополнительные метаданные
    """
    ensure_collection()
    
    model = load_model()
    vector = model.encode(ideological_plan).tolist()
    
    payload = {
        "template_id": template_id,
        "doctor_id": doctor_id,
        "ideological_plan": ideological_plan,
        "codes_sequence": codes_sequence,
        "metadata": metadata or {},
    }
    
    client = get_qdrant_client()
    client.upsert(
        collection_name=PLAN_TEMPLATES_COLLECTION,
        points=[models.PointStruct(
            id=template_id,
            vector=vector,
            payload=payload,
        )],
    )
    logger.info(f"Saved template {template_id} to Qdrant")


def search_similar_templates(
    query: str,
    doctor_id: int,
    top_k: int = 3,
    score_threshold: float = 0.7,
) -> List[models.ScoredPoint]:
    """
    Найти похожие шаблоны планов по идеологическому запросу.
    
    Args:
        query: Идеологический запрос (текст диктовки)
        doctor_id: ID врача для фильтрации
        top_k: Количество результатов
        score_threshold: Минимальный порог схожести (0-1)
    
    Returns:
        Список похожих шаблонов с их метаданными
    """
    ensure_collection()
    
    model = load_model()
    query_vector = model.encode(query).tolist()
    
    client = get_qdrant_client()
    
    # Фильтр по doctor_id
    filter_condition = models.Filter(
        must=[
            models.FieldCondition(
                key="doctor_id",
                match=models.MatchValue(value=doctor_id),
            ),
        ],
    )
    
    results = client.search(
        collection_name=PLAN_TEMPLATES_COLLECTION,
        query_vector=query_vector,
        query_filter=filter_condition,
        limit=top_k,
        score_threshold=score_threshold,
    )
    
    return results


def delete_template_embedding(template_id: int) -> None:
    """Удалить шаблон из Qdrant."""
    client = get_qdrant_client()
    if client.collection_exists(PLAN_TEMPLATES_COLLECTION):
        client.delete(
            collection_name=PLAN_TEMPLATES_COLLECTION,
            points_selector=models.PointIdsList(
                points=[template_id],
            ),
        )
        logger.info(f"Deleted template {template_id} from Qdrant")

