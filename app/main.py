from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from pathlib import Path
import asyncio
import os

import pandas as pd
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from agent.graph import compiled_agent

BASE_DIR = Path(os.getenv("DENT_AI_BASE", Path(__file__).resolve().parents[1]))
CSV_PATH = Path(os.getenv("PRICING_CSV_PATH", BASE_DIR / "staging_price_items.csv"))
COLLECTION = os.getenv("QDRANT_COLLECTION", "price_items_v1")
MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "cointegrated/rubert-tiny2")
model = SentenceTransformer(MODEL_NAME)

def _make_qdrant_client() -> QdrantClient:
    qdrant_url = os.getenv("QDRANT_URL")
    if qdrant_url:
        return QdrantClient(url=qdrant_url)
    host = os.getenv("QDRANT_HOST", "qdrant")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port)

client = _make_qdrant_client()

app = FastAPI(title="Dent AI Pricing API")

class CodeRequest(BaseModel):
    code: str

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5

class PriceItem(BaseModel):
    code: str
    display_name: str
    base_price: float
    section: str
    score: float | None = None

class PlanRequest(BaseModel):
    codes: List[str]

class PlanItem(PriceItem):
    count: int
    sum: float

class PlanResponse(BaseModel):
    items: List[PlanItem]
    total: float

class AgentDraftRequest(BaseModel):
    doctor: str
    patient: str
    card: str | None = ""
    intake: str = ""
    codes: List[str] = []

class AgentDraftResponse(BaseModel):
    plan_draft: str

def load_items() -> pd.DataFrame:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")
    return pd.read_csv(CSV_PATH, dtype={"code": str})

@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.post("/code", response_model=List[PriceItem])
def search_code(payload: CodeRequest):
    df = load_items()
    matches = df.loc[df["code"] == payload.code]
    if matches.empty:
        raise HTTPException(status_code=404, detail="Code not found")
    items = [
        PriceItem(
            code=row.code,
            display_name=row.display_name,
            base_price=float(row.base_price),
            section=row.section,
        )
        for row in matches.itertuples()
    ]
    return items

@app.post("/search", response_model=List[PriceItem])
def search_query(payload: QueryRequest):
    vector = model.encode(payload.query)
    results = client.search(
        collection_name=COLLECTION,
        query_vector=vector,
        limit=payload.top_k,
    )
    items = []
    for point in results:
        data = point.payload or {}
        items.append(
            PriceItem(
                code=str(data.get("code", "")),
                display_name=str(data.get("display_name", "")),
                base_price=float(data.get("base_price", 0)),
                section=str(data.get("section", "")),
                score=float(point.score) if point.score is not None else None,
            )
        )
    return items

@app.post("/plan", response_model=PlanResponse)
def build_plan(payload: PlanRequest):
    df = load_items()
    rows = []
    for code in payload.codes:
        match = df.loc[df["code"] == code]
        if match.empty:
            raise HTTPException(status_code=404, detail=f"Code {code} not found")
        rows.append(match.iloc[0])

    plan_df = pd.DataFrame(rows)
    plan_df["count"] = 1
    collapsed = (
        plan_df.groupby(["code", "display_name", "base_price", "section"], as_index=False)["count"].sum()
    )
    collapsed["sum"] = collapsed["base_price"] * collapsed["count"]

    items = [
        PlanItem(
            code=row.code,
            display_name=row.display_name,
            base_price=float(row.base_price),
            section=row.section,
            count=int(row.count),
            sum=float(row.sum),
        )
        for row in collapsed.itertuples()
    ]
    total = float(collapsed["sum"].sum())
    return PlanResponse(items=items, total=total)

@app.post("/agent/draft")
async def agent_draft(payload: AgentDraftRequest) -> Dict[str, Any]:
    state = {
        "doctor": payload.doctor,
        "patient": payload.patient,
        "card": payload.card or "",
        "intake": payload.intake,
        "codes": payload.codes or [],
        "pricing": [],
        "plan_draft": "",
        "comments": payload.intake,
    }

    result_state = await asyncio.to_thread(compiled_agent.invoke, state)

    return {
        "plan": result_state.get("plan_draft", ""),
        "pricing": result_state.get("pricing", []),
        "validation": result_state.get("validation", []),
    }
