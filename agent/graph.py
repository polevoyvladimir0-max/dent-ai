from __future__ import annotations

from typing import Dict, List, Any, Optional

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

from langchain_openai import ChatOpenAI
import os

from db import SessionLocal, Doctor, DoctorProfile, PlanFeedback, TreatmentPlan
from agent.validators import run_rules

from scripts.search_price import search_by_query, load_items, match_guideline

# Для MVP используем openai/gpt-4o-mini или мок с ReAct. Здесь создаём ллм-клиент,
# но реальный ключ надо положить в окружение OPENAI_API_KEY

api_key = os.getenv("OPENAI_API_KEY")
if api_key:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
else:
    llm = None

class AgentState(dict):
    doctor: str
    doctor_id: Optional[int]
    patient: str
    card: str
    intake: str
    codes: List[str]
    pricing: List[Dict[str, Any]]
    plan_draft: str
    comments: str
    doctor_profile: Optional[Dict[str, Any]]
    doctor_feedback: List[Dict[str, Any]]
    validation: List[Dict[str, Any]]

def collect_context(state: AgentState) -> AgentState:
    doctor_name = state.get("doctor")
    if not doctor_name:
        return state

    with SessionLocal() as session:
        doctor: Optional[Doctor] = (
            session.query(Doctor).filter(Doctor.name == doctor_name).first()
        )
        if not doctor:
            return state

        state["doctor_id"] = doctor.id
        profile_payload: Dict[str, Any] = {
            "specialization": doctor.specialization,
            "experience_years": doctor.experience_years,
            "preferences": doctor.preferences or {},
        }

        profile: Optional[DoctorProfile] = (
            session.query(DoctorProfile)
            .filter(DoctorProfile.doctor_id == doctor.id)
            .order_by(DoctorProfile.updated_at.desc())
            .first()
        )
        if profile:
            profile_payload.update(
                {
                    "profile_name": profile.profile_name,
                    "llm_prompt": profile.llm_prompt,
                    "pricing_bias": profile.pricing_bias or {},
                    "protocol_overrides": profile.protocol_overrides or {},
                }
            )
        state["doctor_profile"] = profile_payload

        feedback: List[PlanFeedback] = (
            session.query(PlanFeedback)
            .join(TreatmentPlan, PlanFeedback.plan_id == TreatmentPlan.id)
            .filter(PlanFeedback.doctor_id == doctor.id)
            .order_by(PlanFeedback.created_at.desc())
            .limit(5)
            .all()
        )
        state["doctor_feedback"] = [
            {
                "rating": fb.rating,
                "accepted": fb.accepted,
                "comments": fb.comments,
                "diff": fb.diff_json,
            }
            for fb in feedback
        ]

    return state

def retrieve_pricing(state: AgentState) -> AgentState:
    codes = state.get("codes") or []
    intake = state.get("intake") or ""

    # приоритет — явные коды
    pricing_rows: List[Dict[str, Any]] = []
    if codes:
        df = load_items()
        for code in codes:
            row = df.loc[df["code"] == code]
            if not row.empty:
                pricing_rows.append(row.iloc[0].to_dict())

    # если кодов нет или часть не найдена — делаем семантический поиск
    if not pricing_rows and intake:
        try:
            matches = search_by_query(intake, top_k=5)
        except Exception:
            matches = []
        for match in matches:
            payload = match.payload or {}
            pricing_rows.append(payload)

    for entry in pricing_rows:
        code = entry.get("code")
        guideline = match_guideline(code) if code else None
        if guideline:
            entry.setdefault("guideline_summary", guideline.get("summary"))
            entry.setdefault("guideline_ref", guideline.get("reference"))

    state["pricing"] = pricing_rows
    return state

def generate_stub_plan(state: AgentState) -> str:
    doctor = state.get("doctor", "")
    patient = state.get("patient", "")
    intake = state.get("intake", "")
    codes = ", ".join(state.get("codes", []))
    return (
        f"Доктор {doctor} предложил базовый план для {patient}.\n"
        f"Коды услуг: {codes}.\n"
        f"Описание: {intake}.\n"
        "Шаги:\n"
        "1. Консультация и диагностика.\n"
        "2. Лечение согласно кодам.\n"
        "3. Контрольный визит."
    )

def build_plan(state: AgentState) -> AgentState:
    if llm is None:
        state["plan_draft"] = generate_stub_plan(state)
        return state

    doctor = state.get("doctor", "")
    patient = state.get("patient", "")
    intake = state.get("intake", "")
    codes = ", ".join(state.get("codes", []))

    doctor_profile = state.get("doctor_profile") or {}
    doctor_prefs = doctor_profile.get("preferences") or {}
    specialization = doctor_profile.get("specialization")
    base_prompt = doctor_profile.get("llm_prompt")

    feedback_section = ""
    feedback_list = state.get("doctor_feedback") or []
    if feedback_list:
        serialized = []
        for item in feedback_list:
            serialized.append(
                f"accepted={item['accepted']} rating={item['rating']} comments={item['comments']} diff={item['diff']}"
            )
        feedback_section = "\nНедавние корректировки врача: " + " | ".join(serialized)

    preference_section = ""
    if doctor_prefs:
        preference_section += "\nПредпочтения врача: " + str(doctor_prefs)
    if specialization:
        preference_section += f"\nСпециализация врача: {specialization}"

    system_prompt = base_prompt or (
        "Ты ассистент стоматологической клиники. Составь план лечения на русском языке,"
        " учитывая предоставленные данные, специализацию врача и его недавние корректировки."
        " Включи поэтапное описание, бюджет с диапазоном и рекомендации пациенту."
    )

    human_prompt = (
        f"Доктор: {doctor}\nПациент: {patient}\nКоды услуг: {codes}"
        f"\nОписание консультации: {intake}{preference_section}{feedback_section}"
    )

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ])
    state["plan_draft"] = response.content
    return state

def finalize(state: AgentState) -> AgentState:
    plan_text = state.get("plan_draft", "")
    codes = state.get("codes", [])
    pricing = state.get("pricing", [])

    context = {
        "plan_text": plan_text,
        "codes": codes,
        "pricing": pricing,
    }

    results = run_rules(context)
    state["validation"] = [
        {
            "rule_id": res.rule_id,
            "passed": res.passed,
            "message": res.message,
            "severity": res.severity,
        }
        for res in results
    ]

    return state

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("collect_context", collect_context)
    graph.add_node("retrieve_pricing", retrieve_pricing)
    graph.add_node("build_plan", build_plan)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("collect_context")
    graph.add_edge("collect_context", "retrieve_pricing")
    graph.add_edge("retrieve_pricing", "build_plan")
    graph.add_edge("build_plan", "finalize")
    graph.add_edge("finalize", END)
    return graph

agent_graph = build_graph()
compiled_agent = agent_graph.compile()
