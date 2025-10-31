import pandas as pd
from pathlib import Path
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.http import models

CSV_PATH = Path(r"C:\dent_ai\staging_price_items.csv")
COLLECTION = "price_items_v1"
MODEL_NAME = "cointegrated/rubert-tiny2"

if not CSV_PATH.exists():
    raise FileNotFoundError(f"Не найден CSV: {CSV_PATH}")

items = pd.read_csv(CSV_PATH, dtype={"code": str})
items["section"] = items["section"].fillna("")
items["display_name"] = items["display_name"].fillna("")

items["text"] = (
    items["display_name"]
    + " | код " + items["code"]
    + " | раздел " + items["section"]
    + items["base_price"].map(lambda x: f" | {x:.2f} RUB")
)

print("Записей в прайсе:", len(items))

model = SentenceTransformer(MODEL_NAME)
embeddings = model.encode(items["text"].tolist(), show_progress_bar=True)

client = QdrantClient(host="127.0.0.1", port=6333)

if client.collection_exists(COLLECTION):
    client.delete_collection(COLLECTION)

client.create_collection(
    collection_name=COLLECTION,
    vectors_config=models.VectorParams(size=embeddings.shape[1], distance=models.Distance.COSINE),
)

payloads = items[["code", "display_name", "section", "base_price"]].to_dict("records")
points = [
    models.PointStruct(id=int(idx), vector=embeddings[idx], payload=payloads[idx])
    for idx in range(len(items))
]

client.upload_points(collection_name=COLLECTION, points=points)

print("Готово:", len(items), "записей в Qdrant")
