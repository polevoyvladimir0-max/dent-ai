import asyncio
import logging
import os
import re
from contextlib import suppress, contextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    FSInputFile,
    Message,
    Voice,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)

from faster_whisper import WhisperModel

from .config import BotConfig
from .utils.llm import YandexGPTClient
from pdf_generator import generate_pdf
from db import SessionLocal, Doctor, Patient, Session as DBSession, TreatmentPlan, PlanFeedback
from scripts.search_price import load_items, search_by_query, warm_up
import json

AGENT_TIMEOUT_SECONDS = 25.0

BASE_DIR = Path(os.getenv("DENT_AI_BASE", Path(__file__).resolve().parents[1]))
ALIASES_PATH = Path(os.getenv("SERVICE_ALIASES_PATH", BASE_DIR / "config" / "service_aliases.json"))
if ALIASES_PATH.exists():
    with ALIASES_PATH.open("r", encoding="utf-8") as fh:
        SERVICE_ALIASES = json.load(fh)
else:
    SERVICE_ALIASES = {}


def match_aliases(query: str) -> List[str]:
    query_lower = query.lower()
    matched_codes = []
    for alias, codes in SERVICE_ALIASES.items():
        if alias in query_lower:
            matched_codes.extend(codes)
    return matched_codes


logging.basicConfig(level=logging.INFO)


class SemanticSearchUnavailable(Exception):
    pass

config = BotConfig.from_env()
bot = Bot(token=config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

VOICE_MODEL_NAME = os.getenv("WHISPER_MODEL", "small")
VOICE_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
VOICE_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE", "int8_float32")
VOICE_MODEL = WhisperModel(VOICE_MODEL_NAME, device=VOICE_DEVICE, compute_type=VOICE_COMPUTE_TYPE)
AUDIO_DIR = Path(os.getenv("VOICE_STORAGE", BASE_DIR / "voice"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

SEMANTIC_TIMEOUT_SECONDS = float(os.getenv("SEMANTIC_TIMEOUT_SECONDS", "6.0"))

HELP_SNIPPETS = [
    "Планирование синус-лифтинга: 'Открытый синус-лифтинг справа, имплантаты Straumann'",
    "Ортопедия: 'Две коронки e.max, одна коронка металлокерамика на 3.6'",
    "Детская стоматология: 'Лечение кариеса молочного зуба, герметизация фиссур'",
    "Ортодонтия: 'Брекет-система Damon, активация дуги'",
    "Пародонтология: 'Вектор-терапия, закрытый кюретаж 4 карманов'",
]


CONFIRM_WORDS = {
    "да",
    "подтвердить",
    "завершить",
    "ок",
    "окей",
    "готово",
    "принять",
    "yes",
    "y",
    "done",
    "finish",
}

DECLINE_WORDS = {
    "нет",
    "не",
    "неа",
    "изменить",
    "правки",
    "редактировать",
    "отклонить",
    "нужны правки",
}

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый план"), KeyboardButton(text="Обновить профиль")],
        [KeyboardButton(text="Подсказки"), KeyboardButton(text="Оценить план")],
    ],
    resize_keyboard=True,
)

HELP_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Назад")]],
    resize_keyboard=True,
)

FEEDBACK_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Принято"), KeyboardButton(text="Нужны правки")],
        [KeyboardButton(text="Назад")],
    ],
    resize_keyboard=True,
)

def build_help_message() -> str:
    rows = "\n".join(f"{idx + 1}. {snippet}" for idx, snippet in enumerate(HELP_SNIPPETS))
    return (
        "⚡ Быстрые подсказки:\n"
        f"{rows}\n\n"
        "💡 Можно комбинировать голос и текст: сначала описываешь кейс, потом уточняешь коды или материалы."
    )

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class SessionState(StatesGroup):
    doctor_name = State()
    doctor_specialization = State()
    doctor_degree = State()
    doctor_category = State()
    doctor_experience = State()
    patient = State()
    card_number = State()
    intake = State()
    plan_codes = State()
    plan_disambiguation = State()
    plan_confirm = State()
    plan_feedback_rating = State()
    plan_feedback_comment = State()


def parse_choice_indexes(raw: str) -> List[int]:
    tokens = [token.strip() for token in re.split(r"[\s,;]+", raw) if token.strip()]
    indexes: List[int] = []
    for token in tokens:
        if token.isdigit():
            idx = int(token) - 1
            if idx >= 0:
                indexes.append(idx)
    return indexes


async def fetch_plan_summary(codes: List[str]) -> dict:
    payload = {"codes": codes}
    timeout = aiohttp.ClientTimeout(total=float(os.getenv("PLAN_API_TIMEOUT", "15")))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{config.api_base_url}/plan", json=payload) as resp:
            if resp.status == 404:
                data = await resp.json()
                detail = data.get("detail") if isinstance(data, dict) else None
                raise ValueError(detail or "Код не найден в прайсе")
            resp.raise_for_status()
            return await resp.json()


def parse_codes(raw_codes: str) -> List[str]:
    tokens = [token.strip() for token in re.split(r"[\s,;]+", raw_codes) if token.strip()]
    return [token for token in tokens if token.isdigit()]


def format_doctor_display(doctor: Doctor) -> str:
    prefs = doctor.preferences or {}
    return format_doctor_display_obj(
        name=doctor.name,
        specialization=doctor.specialization,
        degree=prefs.get("degree"),
        category=prefs.get("category"),
        experience=doctor.experience_years,
    )


def format_doctor_display_obj(
    name: str,
    specialization: Optional[str],
    degree: Optional[str],
    category: Optional[str],
    experience: Optional[float],
) -> str:
    parts = []
    if specialization:
        parts.append(f"врач {specialization}")
    if category and category.lower() != "нет":
        parts.append(f"{category} категории")
    if degree and degree.lower() != "нет":
        parts.append(degree)
    if experience:
        parts.append(f"стаж {experience:g} лет")

    header = ", ".join(parts) if parts else "врач"
    return f"{header} {name}".strip()

async def suggest_codes_from_text(text_query: str) -> List[Dict[str, Any]]:
    alias_codes = match_aliases(text_query)
    results: List[Dict[str, Any]] = []
    if alias_codes:
        df = load_items_cached()
        for code in alias_codes:
            row = df.get(code)
            if row:
                results.append(row)
    if results:
        return results

    loop = asyncio.get_running_loop()

    def _search():
        return search_by_query(text_query, top_k=7)

    try:
        results = await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=SEMANTIC_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as timeout_err:
        logging.error("Semantic search timed out for query '%s'", text_query)
        raise SemanticSearchUnavailable("semantic timeout") from timeout_err
    except Exception as exc:
        logging.exception("Semantic search failed for query: %s", text_query)
        raise SemanticSearchUnavailable("semantic failure") from exc

    seen_codes = set()
    suggestions: List[Dict[str, Any]] = []
    for point in results:
        payload = point.payload or {}
        code = str(payload.get("code", "")).strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        suggestions.append(
            {
                "code": code,
                "display_name": payload.get("display_name", ""),
                "base_price": payload.get("base_price", 0),
                "section": payload.get("section", ""),
                "score": point.score,
            }
        )
    return suggestions


def load_items_cached() -> Dict[str, Dict[str, Any]]:
    if not hasattr(load_items_cached, "_cache"):
        df = load_items()
        cache = {}
        for row in df.itertuples():
            cache[str(row.code)] = {
                "code": str(row.code),
                "display_name": row.display_name,
                "base_price": float(row.base_price),
                "section": row.section,
                "score": None,
            }
        load_items_cached._cache = cache
    return load_items_cached._cache


try:
    logging.info("Preloading semantic search resources...")
    load_items_cached()
    warm_up()
except Exception as preload_exc:
    logging.warning("Semantic search warm-up failed: %s", preload_exc)


LLM_ENABLED = bool(config.yandex_gpt_api_key and config.yandex_gpt_folder_id)
LLM_TYPING_INTERVAL = 2.5
LLM_CLIENT = (
    YandexGPTClient(
        api_key=config.yandex_gpt_api_key,
        folder_id=config.yandex_gpt_folder_id,
        model=config.yandex_gpt_model,
        timeout=config.yandex_gpt_timeout,
    )
    if LLM_ENABLED
    else None
)


def shorten_text(value: str, limit: int = 320) -> str:
    clean = (value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


async def _typing_loop(chat_id: int) -> None:
    while True:
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(LLM_TYPING_INTERVAL)


async def with_typing_action(chat_id: int, coro):
    task = asyncio.create_task(_typing_loop(chat_id))
    try:
        return await coro
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def build_smalltalk_prompts(scenario: str, **context: Any) -> Optional[tuple[str, str]]:
    if scenario == "greeting":
        doctor = context.get("doctor") or "коллега"
        specialization = context.get("specialization")
        spec_text = f", {specialization}" if specialization else ""
        return (
            "Ты дружелюбный и профессиональный ассистент стоматолога. Говори кратко: одно-два предложения.",
            f"Доктор {doctor}{spec_text} вернулся в чат и готов составлять планы. Поздоровайся и напомни, что помогаешь с кодами и идеями.",
        )
    if scenario == "intake_ack":
        intake = shorten_text(context.get("intake", ""))
        if not intake:
            return None
        return (
            "Ты ассистент стоматолога, поддерживаешь рабочий диалог и отвечаешь коротко.",
            f"Я получил диктовку плана: '{intake}'. Ответь, что понял задачу и готов подобрать услуги или коды.",
        )
    if scenario == "codes_ack":
        codes: List[str] = context.get("codes", [])
        if not codes:
            return None
        preview = ", ".join(codes[:5])
        return (
            "Ты помогаешь врачу оперативно собирать план. Отвечай коротко.",
            f"Доктор прислал коды {preview}. Ответь, что приступаешь к расчёту и можно добавлять другие позиции, если потребуется.",
        )
    if scenario == "suggestions":
        count = context.get("count", 0)
        query = shorten_text(context.get("query", ""))
        if not query or not count:
            return None
        return (
            "Ты ассистент стоматолога. Отвечай по делу, до двух предложений.",
            f"Я нашёл {count} подходящих услуг по описанию '{query}'. Подскажи врачу, что можно выбрать варианты из списка или уточнить запрос.",
        )
    if scenario == "plan_summary":
        total = context.get("total")
        items = context.get("items", 0)
        if total is None:
            return None
        try:
            total_text = f"{float(total):.2f} ₽"
        except Exception:
            total_text = str(total)
        return (
            "Ты ассистент стоматолога, комментируешь результат кратко.",
            f"План обновлён: {items} позиций, сумма {total_text}. Попроси подтвердить или продолжить работу.",
        )
    return None


async def maybe_smalltalk(message: Message, scenario: str, reply_markup=None, **context: Any) -> None:
    if not LLM_CLIENT:
        return
    prompts = build_smalltalk_prompts(scenario, **context)
    if not prompts:
        return
    system_prompt, user_prompt = prompts
    try:
        response = await with_typing_action(
            message.chat.id,
            asyncio.wait_for(LLM_CLIENT.smalltalk(system_prompt, user_prompt), config.yandex_gpt_timeout),
        )
    except Exception as exc:
        logging.debug("LLM smalltalk failed (%s): %s", scenario, exc)
        return
    if response:
        await message.answer(response, reply_markup=reply_markup)


async def process_codes(message: Message, state: FSMContext, codes: List[str]) -> None:
    data = await state.get_data()
    existing_codes: List[str] = data.get("codes", [])
    all_codes = existing_codes + codes

    await message.answer("⚙️ Считаю суммы по прайсу...", reply_markup=MAIN_KEYBOARD)
    try:
        new_plan = await fetch_plan_summary(codes)
    except ValueError as err:
        await message.answer(f"⚠️ {err}. Уточни услуги или выбери другие позиции.")
        return
    except Exception as exc:
        logging.exception("Plan fetch failed")
        await message.answer(f"Не удалось получить план: {exc}. Повтори или измени коды.")
        return

    session_id = data.get("db_session_id")
    plan_id = data.get("plan_id")
    combined_plan = combine_plans(data.get("plan"), new_plan, all_codes)

    with get_db() as db:
        session_record = db.get(DBSession, session_id) if session_id else None
        if not session_record:
            session_record = DBSession(
                doctor_id=data["doctor_id"],
                patient_id=data["patient_id"],
                transcript=data.get("intake", ""),
                status="draft",
                codes=" ".join(all_codes),
            )
            db.add(session_record)
            db.flush()
            session_id = session_record.id
        else:
            session_record.codes = " ".join(all_codes)
            session_record.transcript = data.get("intake", "")

        plan_record = db.get(TreatmentPlan, plan_id) if plan_id else None
        if not plan_record:
            plan_record = TreatmentPlan(
                session_id=session_record.id,
                status="draft",
                plan_json=combined_plan,
            )
            db.add(plan_record)
            db.flush()
            plan_id = plan_record.id
        else:
            plan_record.plan_json = combined_plan
            plan_record.status = "draft"
        db.commit()

    summary = format_plan(combined_plan)
    await state.update_data(plan=combined_plan, codes=all_codes, db_session_id=session_id, plan_id=plan_id)

    agent_payload = {
        "doctor": data.get("doctor_full_display") or data.get("doctor") or "",
        "patient": data.get("patient", ""),
        "card": data.get("card", ""),
        "intake": data.get("intake", ""),
        "codes": all_codes,
    }
    agent_result = await call_agent_draft(agent_payload)
    if agent_result:
        with get_db() as db:
            plan_record = db.get(TreatmentPlan, plan_id) if plan_id else None
            if plan_record:
                plan_record.agent_plan = agent_result.get("plan")
                plan_record.agent_validation = agent_result.get("validation")
                db.commit()
        await state.update_data(agent_result=agent_result)
        agent_text = format_agent_feedback(agent_result)
    else:
        await state.update_data(agent_result=None)
        agent_text = "🤖 Ассистент недоступен."

    await message.answer(summary, reply_markup=MAIN_KEYBOARD)
    await message.answer(agent_text)
    await maybe_smalltalk(
        message,
        "plan_summary",
        reply_markup=None,
        total=combined_plan.get("total"),
        items=len(combined_plan.get("items", [])),
    )
    await message.answer(
        "Продолжить добавление услуг или завершить план? Напиши 'продолжить' или 'завершить'.",
        reply_markup=MAIN_KEYBOARD,
    )
    await state.set_state(SessionState.plan_confirm)


async def finalize_current_plan(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan = data.get("plan")
    if not plan or not plan.get("items"):
        await message.answer(
            "План пустой. Добавь услуги или опиши их словами, чтобы я сформировал финальную версию.",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.set_state(SessionState.plan_codes)
        return

    patient = data.get("patient", "Пациент")
    card = data.get("card", "-")
    doctor_display = data.get("doctor_full_display") or data.get("doctor") or "Врач"
    doctor_plain = data.get("doctor") or doctor_display

    pdf_path = generate_pdf(
        plan,
        doctor_plain,
        patient,
        card,
        full_doctor_title=data.get("doctor_full_display"),
    )

    session_id = data.get("db_session_id")
    plan_id = data.get("plan_id")

    with get_db() as db:
        if plan_id:
            plan_record = db.get(TreatmentPlan, plan_id)
            if plan_record:
                plan_record.plan_json = plan
                plan_record.pdf_path = str(pdf_path)
                plan_record.status = "final"
                session_record = db.get(DBSession, plan_record.session_id)
                if session_record:
                    session_record.status = "final"
                db.commit()
        elif session_id:
            session_record = db.get(DBSession, session_id)
            if session_record:
                session_record.status = "final"
                db.commit()

    total_value = float(plan.get("total", 0) or 0)
    caption = f"Готово. Итоговая сумма: {total_value:.2f} ₽"
    await message.answer_document(FSInputFile(str(pdf_path)), caption=caption)

    base_state = {key: data[key] for key in ("doctor", "doctor_id", "doctor_full_display") if data.get(key)}
    await state.set_data(base_state)
    await message.answer(
        "План сохранён. Укажи следующего пациента или нажми 'Новый план'.",
        reply_markup=MAIN_KEYBOARD,
    )
    await state.set_state(SessionState.patient)


def combine_plans(existing: Optional[dict], new_part: dict, order_sequence: List[str]) -> dict:
    existing_items = (existing or {}).get("items", [])
    new_items = new_part.get("items", [])
    merged: Dict[str, Dict[str, Any]] = {}

    def add_item(item: Dict[str, Any]) -> None:
        code = item.get("code")
        if not code:
            return
        entry = merged.setdefault(
            code,
            {
                "code": code,
                "display_name": item.get("display_name", ""),
                "section": item.get("section", ""),
                "base_price": float(item.get("base_price", 0)),
                "count": 0,
            },
        )
        entry["count"] += int(item.get("count", 0))

    for item in existing_items:
        add_item(item)
    for item in new_items:
        add_item(item)

    order_index: Dict[str, int] = {}
    for idx, code in enumerate(order_sequence):
        if code not in order_index:
            order_index[code] = idx

    items = []
    total = 0.0
    for entry in merged.values():
        entry_sum = entry["base_price"] * entry["count"]
        total += entry_sum
        entry_dict = {
            "code": entry["code"],
            "display_name": entry["display_name"],
            "section": entry["section"],
            "base_price": entry["base_price"],
            "count": entry["count"],
            "sum": entry_sum,
        }
        items.append(entry_dict)

    items.sort(key=lambda item: order_index.get(item["code"], len(order_index)))

    return {"items": items, "total": total}


def format_plan(plan: dict) -> str:
    lines = []
    for item in plan.get("items", []):
        code = item.get("code", "")
        name = item.get("display_name", "")
        count = item.get("count", 1)
        item_sum = item.get("sum", 0)
        lines.append(f"• {code}: {name} × {count} → {item_sum} ₽")
    total = plan.get("total", 0)
    body = "\n".join(lines) if lines else "(пусто)"
    return f"{body}\n\nИтого: {total} ₽"

async def call_agent_draft(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        timeout = aiohttp.ClientTimeout(total=AGENT_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{config.api_base_url}/agent/draft",
                json=payload,
            ) as resp:
                if resp.status >= 500:
                    logging.error("Agent draft failed with status %s", resp.status)
                    return None
                if resp.status == 404:
                    logging.warning("Agent draft returned 404 for payload %s", payload)
                    return None
                resp.raise_for_status()
                return await resp.json()
    except asyncio.TimeoutError:
        logging.error("Agent draft timeout for payload %s", payload)
    except Exception:
        logging.exception("Agent draft request crashed")
    return None


def format_agent_feedback(agent_result: Dict[str, Any]) -> str:
    parts = []
    plan_text = agent_result.get("plan")
    if plan_text:
        parts.append("🤖 Черновик ассистента:\n" + plan_text.strip())
    validation = agent_result.get("validation") or []
    if validation:
        issues = []
        for item in validation:
            if not item:
                continue
            status = "✅" if item.get("passed") else "⚠️"
            issues.append(f"{status} {item.get('message', '')}")
        if issues:
            parts.append("🔍 Проверки:\n" + "\n".join(issues))
    if not parts:
        return "🤖 Ассистент не дал новых рекомендаций."
    return "\n\n".join(parts)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = str(message.from_user.id)
    with get_db() as db:
        doctor = db.query(Doctor).filter_by(telegram_id=telegram_id).one_or_none()

    await state.clear()

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="profile", description="Обновить профиль врача"),
            BotCommand(command="newplan", description="Создать новый план"),
        ]
    )

    if doctor and doctor.specialization:
        await state.update_data(
            doctor=doctor.name,
            doctor_id=doctor.id,
            doctor_full_display=format_doctor_display(doctor),
        )
        await message.answer(
            f"👋 Привет, {doctor.name}! Продолжаем. Укажи пациента (ФИО/ID).",
            reply_markup=MAIN_KEYBOARD,
        )
        await maybe_smalltalk(
            message,
            "greeting",
            reply_markup=None,
            doctor=doctor.name,
            specialization=doctor.specialization,
        )
        await state.set_state(SessionState.patient)
        return

    await message.answer(
        "👋 Привет! Давай настроим профиль. Введи ФИО полностью.",
        reply_markup=MAIN_KEYBOARD,
    )
    await state.set_state(SessionState.doctor_name)

@dp.message(SessionState.doctor_name)
async def handle_doctor_name(message: Message, state: FSMContext):
    doctor_name = message.text.strip()
    telegram_id = str(message.from_user.id)
    with get_db() as db:
        doctor = db.query(Doctor).filter_by(telegram_id=telegram_id).one_or_none()
        if doctor:
            doctor.name = doctor_name
        else:
            doctor = Doctor(name=doctor_name, telegram_id=telegram_id)
            db.add(doctor)
        db.commit()
        doctor_id = doctor.id

    await state.update_data(doctor=doctor_name, doctor_id=doctor_id)
    await message.answer("Укажи специализацию (например: стоматолог-ортопед).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_specialization)


@dp.message(SessionState.doctor_specialization)
async def handle_specialization(message: Message, state: FSMContext):
    specialization = message.text.strip()
    await state.update_data(specialization=specialization)
    await message.answer("Ученая степень (например: к.м.н. или напиши 'нет').", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_degree)


@dp.message(SessionState.doctor_degree)
async def handle_degree(message: Message, state: FSMContext):
    degree = message.text.strip()
    await state.update_data(degree=degree)
    await message.answer("Квалификационная категория (высшая/первая/вторая/нет).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_category)


@dp.message(SessionState.doctor_category)
async def handle_category(message: Message, state: FSMContext):
    category = message.text.strip()
    await state.update_data(category=category)
    await message.answer("Стаж (в годах).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_experience)


@dp.message(SessionState.doctor_experience)
async def handle_experience(message: Message, state: FSMContext):
    raw = message.text.strip()
    try:
        experience = float(raw.replace(',', '.'))
    except ValueError:
        await message.answer("Не понял стаж. Введи число, например 12 или 12.5")
        return

    data = await state.get_data()
    telegram_id = str(message.from_user.id)

    with get_db() as db:
        doctor = db.get(Doctor, data["doctor_id"])
        if doctor:
            doctor.specialization = data.get("specialization")
            doctor.preferences = doctor.preferences or {}
            doctor.preferences.update(
                {
                    "degree": data.get("degree"),
                    "category": data.get("category"),
                }
            )
            doctor.experience_years = experience
            db.commit()

    display = format_doctor_display_obj(
        name=data["doctor"],
        specialization=data.get("specialization"),
        degree=data.get("degree"),
        category=data.get("category"),
        experience=experience,
    )

    await state.update_data(doctor_full_display=display)
    await message.answer(
        f"✅ Профиль готов: {display}\nТеперь укажи пациента (ФИО/ID).",
        reply_markup=MAIN_KEYBOARD,
    )
    await state.set_state(SessionState.patient)

@dp.message(SessionState.patient)
async def handle_patient(message: Message, state: FSMContext):
    patient_name = message.text.strip()
    await state.update_data(patient=patient_name)
    await message.answer("📄 Номер амбулаторной карты?", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.card_number)

@dp.message(SessionState.card_number)
async def handle_card(message: Message, state: FSMContext):
    card_number = message.text.strip()
    data = await state.get_data()
    patient_name = data.get("patient", "")
    with get_db() as db:
        patient = db.query(Patient).filter_by(name=patient_name, card_number=card_number).one_or_none()
        if patient is None:
            patient = Patient(name=patient_name, card_number=card_number)
            db.add(patient)
            db.commit()
        patient_id = patient.id
    await state.update_data(card=card_number, patient_id=patient_id)
    await message.answer("🎙 Надиктуй план лечения (голос или текст).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.intake)

@dp.message(SessionState.intake, F.voice)
async def handle_voice(message: Message, state: FSMContext):
    await message.answer("⌛ Распознаю аудио...")
    file_path = await download_voice(message)
    text = await transcribe_voice(file_path)
    await state.update_data(intake=text)
    await message.answer(
        f"🎙 Распознал: {text}\n\nТеперь отправь коды или опиши услуги (например: 'имплантат Straumann', 'коронка диоксид').",
        reply_markup=MAIN_KEYBOARD,
    )
    await maybe_smalltalk(message, "intake_ack", reply_markup=None, intake=text)
    await state.set_state(SessionState.plan_codes)


@dp.message(SessionState.intake)
async def handle_intake(message: Message, state: FSMContext):
    await state.update_data(intake=message.text.strip())
    await message.answer(
        "Отлично. Теперь отправь коды услуг или опиши словами (например: 'имплантат Straumann').",
        reply_markup=MAIN_KEYBOARD,
    )
    await maybe_smalltalk(message, "intake_ack", reply_markup=None, intake=message.text.strip())
    await state.set_state(SessionState.plan_codes)


@dp.message(SessionState.plan_codes)
async def handle_plan_codes(message: Message, state: FSMContext):
    raw = message.text.strip()

    if not re.search(r"[\wа-яА-ЯёЁ]", raw) or len(raw) < 2:
        await message.answer(
            "Нужна более конкретная формулировка. Опиши услугу словами (например: 'имплантат Straumann').",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    codes = parse_codes(raw)

    if not codes:
        await message.answer("⌛ Ищу услуги по описанию...")
        try:
            picks = await suggest_codes_from_text(raw)
        except SemanticSearchUnavailable:
            await message.answer(
                "⚠️ Не получилось подобрать услуги по описанию. Попробуй сформулировать иначе или введи коды вручную.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        if not picks:
            await message.answer("Не смог найти совпадения. Попробуй уточнить формулировку или указать код.")
            return
        await state.update_data(candidate_codes=picks, raw_text=raw)
        options = "\n".join(
            f"{idx + 1}. {item['code']} — {item['display_name']} ({item['base_price']} ₽)"
            for idx, item in enumerate(picks)
        )
        await message.answer(
            "Нашёл подходящие позиции:\n"
            f"{options}\n\nНапиши номера через запятую (например: 1,3).",
            reply_markup=MAIN_KEYBOARD,
        )
        await maybe_smalltalk(message, "suggestions", reply_markup=None, query=raw, count=len(picks))
        await state.set_state(SessionState.plan_disambiguation)
        return

    await maybe_smalltalk(message, "codes_ack", reply_markup=None, codes=codes)
    await process_codes(message, state, codes)


@dp.message(SessionState.plan_disambiguation)
async def handle_plan_disambiguation(message: Message, state: FSMContext):
    data = await state.get_data()
    candidates: List[Dict[str, Any]] = data.get("candidate_codes", [])
    if not candidates:
        await message.answer("Кандидаты не найдены. Начни ввод кодов заново.")
        await state.set_state(SessionState.plan_codes)
        return

    indexes = parse_choice_indexes(message.text)
    if not indexes:
        await message.answer("Не понял выбор. Укажи номера через запятую, например 1,2.")
        return

    selected: List[str] = []
    for idx in indexes:
        if idx < len(candidates):
            selected.append(candidates[idx]["code"])

    if not selected:
        await message.answer("Ни один номер не распознан. Повтори выбор.")
        return

    await state.update_data(candidate_codes=None, raw_text=None)
    await process_codes(message, state, selected)

@dp.message(SessionState.plan_disambiguation, F.text.func(lambda v: v and v.lower() in {"отмена", "cancel"}))
async def cancel_disambiguation(message: Message, state: FSMContext):
    await state.update_data(candidate_codes=None, raw_text=None)
    await message.answer("Окей, выбери коды заново или опиши услуги ещё раз.", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.plan_codes)


@dp.message(SessionState.plan_confirm, F.text.func(lambda v: v and v.lower() in {"продолжить", "continue"}))
async def plan_continue(message: Message, state: FSMContext):
    await message.answer(
        "Ок, добавим ещё услуги. Напиши коды или опиши словами следующую часть плана.",
        reply_markup=MAIN_KEYBOARD,
    )
    await state.set_state(SessionState.plan_codes)

@dp.message(SessionState.plan_confirm)
async def handle_plan_confirm(message: Message, state: FSMContext):
    raw = (message.text or "").strip().lower()

    if raw in CONFIRM_WORDS:
        await finalize_current_plan(message, state)
        return

    if raw in DECLINE_WORDS:
        await message.answer("🔁 Принято. Надиктуй правки или текст заново.", reply_markup=MAIN_KEYBOARD)
        await state.set_state(SessionState.intake)
        return

    await message.answer(
        "Не понял. Напиши 'продолжить' для добавления услуг, 'завершить' или 'да' для финализации, либо 'нет' чтобы внести правки.",
        reply_markup=MAIN_KEYBOARD,
    )

@dp.message(F.text.lower() == "оценить план")
async def start_feedback(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("plan_id"):
        await message.answer("Пока нечего оценивать. Сначала сформируй план.", reply_markup=MAIN_KEYBOARD)
        return
    await message.answer("Как оцениваешь текущий план?", reply_markup=FEEDBACK_KEYBOARD)
    await state.set_state(SessionState.plan_feedback_rating)

@dp.message(SessionState.plan_feedback_rating, F.text.lower() == "назад")
async def feedback_back_to_menu(message: Message, state: FSMContext):
    await state.set_state(SessionState.plan_confirm)
    await message.answer("Окей, возвращаемся к плану.", reply_markup=MAIN_KEYBOARD)


@dp.message(SessionState.plan_feedback_rating)
async def handle_feedback_rating(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if text not in {"принято", "нужны правки"}:
        await message.answer("Пиши 'Принято' или 'Нужны правки'.", reply_markup=FEEDBACK_KEYBOARD)
        return
    await state.update_data(feedback_rating=text)
    await message.answer("Оставь короткий комментарий (что особенно важно / что доработать).", reply_markup=FEEDBACK_KEYBOARD)
    await state.set_state(SessionState.plan_feedback_comment)


@dp.message(SessionState.plan_feedback_comment, F.text.lower() == "назад")
async def feedback_comment_back(message: Message, state: FSMContext):
    await state.set_state(SessionState.plan_feedback_rating)
    await message.answer("Хорошо, выбери 'Принято' или 'Нужны правки'.", reply_markup=FEEDBACK_KEYBOARD)


@dp.message(SessionState.plan_feedback_comment)
async def handle_feedback_comment(message: Message, state: FSMContext):
    comment = message.text.strip()
    data = await state.get_data()
    plan_id = data.get("plan_id")
    doctor_id = data.get("doctor_id")
    if not plan_id or not doctor_id:
        await message.answer("Не могу сохранить отзыв: нет актуального плана.", reply_markup=MAIN_KEYBOARD)
        await state.set_state(SessionState.plan_confirm)
        return

    accepted = data.get("feedback_rating") == "принято"

    with get_db() as db:
        feedback = PlanFeedback(
            plan_id=plan_id,
            doctor_id=doctor_id,
            accepted=accepted,
            comments=comment,
        )
        db.add(feedback)
        plan = db.get(TreatmentPlan, plan_id)
        if plan:
            plan.status = "final" if accepted else "needs_changes"
        db.commit()

    await state.update_data(feedback_rating=None)
    await state.set_state(SessionState.plan_confirm)
    await message.answer("Спасибо! Отзыв сохранён.", reply_markup=MAIN_KEYBOARD)

@dp.message(Command("newplan"))
@dp.message(F.text.casefold() == "новый план")
async def start_new_plan(message: Message, state: FSMContext):
    data = await state.get_data()
    doctor_id = data.get("doctor_id")
    await state.set_data({key: value for key, value in data.items() if key in {"doctor", "doctor_id", "doctor_full_display"}})
    if not doctor_id:
        await cmd_start(message, state)
        return
    await message.answer("🧑‍⚕️ Давай начнём новый план. Укажи пациента (ФИО/ID).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.patient)


@dp.message(Command("profile"))
@dp.message(F.text.casefold() == "обновить профиль")
async def update_profile(message: Message, state: FSMContext):
    telegram_id = str(message.from_user.id)
    with get_db() as db:
        doctor = db.query(Doctor).filter_by(telegram_id=telegram_id).one_or_none()
        if doctor:
            doctor.specialization = None
            doctor.experience_years = None
            doctor.preferences = {}
            db.commit()
    await message.answer("Обновим профиль. Введи ФИО полностью.", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_name)

@dp.message(Command("help"))
@dp.message(F.text.lower() == "подсказки")
async def show_help(message: Message):
    await message.answer(build_help_message(), reply_markup=HELP_KEYBOARD)


@dp.message(F.text.lower() == "назад")
async def back_to_main(message: Message, state: FSMContext):
    await message.answer("Возвращаю основное меню.", reply_markup=MAIN_KEYBOARD)

async def main():
    with suppress(KeyboardInterrupt, SystemExit):
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
