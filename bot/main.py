import asyncio
import logging
import os
import re
from contextlib import suppress, contextmanager
from datetime import datetime
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

from .config import BotConfig
from .utils.llm import YandexGPTClient
from .utils.voice import download_voice, transcribe_voice
from pdf_generator import generate_pdf
from db import SessionLocal, Doctor, Patient, Session as DBSession, TreatmentPlan, PlanFeedback, PlanTemplate
from scripts.search_price import load_items, search_by_query, warm_up
from scripts.search_plan_templates import save_template_embedding, search_similar_templates
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
    template_selection = State()  # Выбор шаблона плана из истории
    plan_codes = State()
    plan_stage_selection = State()  # Выбор или создание этапа плана лечения
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

async def search_similar_plan_templates(
    ideological_plan: str,
    doctor_id: int,
    top_k: int = 5,  # Увеличиваем с 3 до 5 для большего выбора
    score_threshold: float = 0.35,  # Снижаем порог с 0.5 до 0.35 для более широкого поиска похожих планов
) -> List[Dict[str, Any]]:
    """
    Ищет похожие шаблоны планов в истории врача по идеологическому запросу.
    
    Returns:
        Список похожих шаблонов с их метаданными (codes_sequence, metadata, score)
    """
    if not ideological_plan or not doctor_id:
        return []
    
    try:
        loop = asyncio.get_running_loop()
        
        def _search():
            return search_similar_templates(
                query=ideological_plan,
                doctor_id=doctor_id,
                top_k=top_k,
                score_threshold=score_threshold,
            )
        
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(None, _search),
                timeout=5.0,  # Увеличиваем таймаут до 5 секунд (синхронизируем с вызовами)
            )
        except asyncio.TimeoutError:
            logging.warning(f"Template search timed out for doctor {doctor_id}")
            return []
        except Exception as exc:
            logging.exception(f"Template search failed for doctor {doctor_id}: {exc}")
            return []
        
        templates = []
        for point in results:
            payload = point.payload or {}
            template_id = payload.get("template_id")
            if not template_id:
                continue
            
            templates.append({
                "template_id": template_id,
                "ideological_plan": payload.get("ideological_plan", ""),
                "codes_sequence": payload.get("codes_sequence", []),
                "metadata": payload.get("metadata", {}),
                "score": point.score,
            })
        
        return templates
    except Exception as exc:
        logging.exception(f"Failed to search similar templates: {exc}")
        return []


async def suggest_codes_from_text(text_query: str) -> List[Dict[str, Any]]:
    """
    Поиск услуг по текстовому описанию.
    
    Для длинных описаний (более 50 символов) пытается разбить текст на отдельные услуги
    и ищет каждую услугу отдельно, объединяя результаты.
    """
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
    query_lower = text_query.strip().lower()
    query_len = len(text_query.strip())
    
    # Для длинных описаний (>50 символов) пытаемся разбить на отдельные услуги
    # Разделители: запятая, точка, точка с запятой, "и", "с", "затем"
    if query_len > 50:
        # Пытаемся разбить на части по разделителям
        separators = [r',', r'\.', r';', r'\s+и\s+', r'\s+с\s+', r'\s+затем\s+', r'\s+потом\s+']
        parts = [text_query.strip()]
        for sep in separators:
            new_parts = []
            for part in parts:
                split_parts = re.split(sep, part, flags=re.IGNORECASE)
                new_parts.extend([p.strip() for p in split_parts if p.strip() and len(p.strip()) > 3])
            if len(new_parts) > len(parts):
                parts = new_parts
        
        # Если разбили на несколько частей, ищем каждую отдельно
        if len(parts) > 1:
            logging.info(f"Split long query into {len(parts)} parts: {parts[:3]}...")
            all_suggestions: List[Dict[str, Any]] = []
            seen_codes = set()
            
            # Ищем каждую часть отдельно
            for part in parts:
                if len(part.strip()) < 3:
                    continue
                try:
                    part_result = await suggest_codes_from_text_single(part.strip(), loop)
                    if part_result:
                        for suggestion in part_result:
                            code = suggestion.get("code")
                            if code and code not in seen_codes:
                                seen_codes.add(code)
                                all_suggestions.append(suggestion)
                except Exception as part_exc:
                    logging.debug(f"Failed to search for part '{part}': {part_exc}")
                    continue
            
            # Сортируем по score и возвращаем лучшие результаты
            all_suggestions.sort(key=lambda x: x.get("score", 0), reverse=True)
            return all_suggestions[:10]  # Возвращаем до 10 результатов для длинных описаний
    
    # Для коротких запросов используем обычный поиск
    return await suggest_codes_from_text_single(text_query, loop)


async def suggest_codes_from_text_single(text_query: str, loop: Optional[asyncio.AbstractEventLoop] = None) -> List[Dict[str, Any]]:
    """Поиск услуг по одному текстовому запросу."""
    if loop is None:
        loop = asyncio.get_running_loop()
    
    def _search():
        query_lower = text_query.strip().lower()
        query_len = len(text_query.strip())
        
        # Общие термины (приём, консультация, осмотр) требуют более низкий порог
        general_terms = ["приём", "прием", "консультация", "осмотр", "визит", "приём врача", "приём хирурга"]
        is_general_term = any(term in query_lower for term in general_terms)
        
        # Специфические медицинские термины требуют более строгий порог
        specific_keywords = ["синус", "имплант", "коронка", "удаление", "лифтинг"]
        is_specific_term = any(kw in query_lower for kw in specific_keywords)
        
        # Определяем порог схожести
        if is_general_term:
            # Для общих терминов снижаем порог, чтобы находить больше вариантов
            threshold = 0.25  # Низкий порог для "приём", "консультация" и т.д.
        elif is_specific_term:
            # Для специфических терминов требуем высокий порог
            if query_len < 5:
                threshold = 0.5
            elif query_len < 10:
                threshold = 0.4
            else:
                threshold = 0.35
        elif query_len < 5:
            # Для очень коротких запросов повышаем порог
            threshold = 0.5
        elif query_len < 10:
            # Для коротких запросов средний порог
            threshold = 0.4
        else:
            # Для длинных запросов стандартный порог
            threshold = 0.35
        
        return search_by_query(text_query, top_k=15, score_threshold=threshold)  # Увеличиваем top_k для общих терминов

    def _text_search_fallback() -> List[Dict[str, Any]]:
        """Fallback поиск по тексту в кеше, если Qdrant недоступен."""
        try:
            items_cache = load_items_cached()
            query_lower = text_query.strip().lower()
            query_words = [w for w in query_lower.split() if len(w) > 2]
            
            if not query_words:
                return []
            
            suggestions: List[Dict[str, Any]] = []
            seen_codes = set()
            
            # Простой текстовый поиск по названию и разделу
            for code, item in items_cache.items():
                display_name_lower = (item.get("display_name", "") or "").lower()
                section_lower = (item.get("section", "") or "").lower()
                combined_text = f"{display_name_lower} {section_lower}"
                
                # Проверяем наличие ключевых слов из запроса
                matches = sum(1 for word in query_words if word in combined_text)
                if matches > 0:
                    # Простой score на основе количества совпадений
                    score = matches / len(query_words)
                    if code not in seen_codes:
                        seen_codes.add(code)
                        suggestions.append({
                            "code": code,
                            "display_name": item.get("display_name", ""),
                            "base_price": item.get("base_price", 0),
                            "section": item.get("section", ""),
                            "score": score,
                        })
            
            # Сортируем по score
            suggestions.sort(key=lambda x: x.get("score", 0), reverse=True)
            return suggestions[:7]
        except Exception as fallback_exc:
            logging.warning(f"Text search fallback failed: {fallback_exc}")
            return []

    try:
        results = await asyncio.wait_for(loop.run_in_executor(None, _search), timeout=5.0)
    except asyncio.TimeoutError as timeout_err:
        logging.error("Semantic search timed out for query '%s', trying text search fallback", text_query)
        # Пробуем fallback через текстовый поиск
        fallback_results = await loop.run_in_executor(None, _text_search_fallback)
        if fallback_results:
            logging.info(f"Text search fallback found {len(fallback_results)} results for query '{text_query}'")
            return fallback_results
        raise SemanticSearchUnavailable("semantic timeout") from timeout_err
    except Exception as exc:
        logging.warning("Semantic search failed for query '%s': %s, trying text search fallback", text_query, exc)
        # Пробуем fallback через текстовый поиск
        try:
            fallback_results = await loop.run_in_executor(None, _text_search_fallback)
            if fallback_results:
                logging.info(f"Text search fallback found {len(fallback_results)} results for query '{text_query}'")
                return fallback_results
        except Exception as fallback_exc:
            logging.error(f"Text search fallback also failed: {fallback_exc}")
        raise SemanticSearchUnavailable("semantic failure") from exc

    seen_codes = set()
    suggestions: List[Dict[str, Any]] = []
    query_lower = text_query.lower()
    
    # Определяем тип запроса для фильтрации
    general_terms = ["приём", "прием", "консультация", "осмотр", "визит"]
    is_general_term = any(term in query_lower for term in general_terms)
    specific_keywords = ["синус", "имплант", "коронка", "удаление", "лечение", "анестезия", "лифтинг"]
    is_specific_term = any(kw in query_lower for kw in specific_keywords)
    
    for point in results:
        payload = point.payload or {}
        code = str(payload.get("code", "")).strip()
        if not code or code in seen_codes:
            continue
        
        display_name_lower = (payload.get("display_name", "") or "").lower()
        section_lower = (payload.get("section", "") or "").lower()
        
        # Для общих терминов более мягкая фильтрация
        if is_general_term:
            # Для "приём хирурга" ищем что-то связанное с хирургией или приёмом
            # Проверяем наличие ключевых слов из запроса в названии или разделе
            query_words = [w for w in query_lower.split() if len(w) > 2 and w not in general_terms]
            if query_words:
                # Если есть специфические слова (например, "хирурга"), проверяем их наличие
                keyword_found = any(word in display_name_lower or word in section_lower for word in query_words)
                if not keyword_found and point.score < 0.3:
                    continue  # Пропускаем только если очень низкий score
            # Для общих терминов принимаем результаты с score >= 0.25
            if point.score < 0.25:
                continue
        elif is_specific_term:
            # Для специфических терминов требуем более строгое совпадение
            query_len = len(text_query.strip())
            min_score = 0.5 if query_len < 5 else (0.4 if query_len < 10 else 0.35)
            if point.score < min_score:
                continue
            
            # Проверяем наличие ключевых слов в названии
            keyword_found = any(kw in display_name_lower for kw in specific_keywords if kw in query_lower)
            if not keyword_found and point.score < 0.5:
                continue  # Пропускаем, если нет совпадения ключевых слов и низкий score
        else:
            # Для остальных запросов стандартная фильтрация
            query_len = len(text_query.strip())
            min_score = 0.5 if query_len < 5 else (0.4 if query_len < 10 else 0.35)
            if point.score < min_score:
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
    # Сортируем по score (от большего к меньшему) для лучшей релевантности
    suggestions.sort(key=lambda x: x.get("score", 0), reverse=True)
    return suggestions[:7]  # Возвращаем максимум 7 лучших результатов


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


async def check_commands_in_state(message: Message, state: FSMContext) -> bool:
    """
    Проверяет, является ли сообщение командой, и обрабатывает её.
    Возвращает True, если команда была обработана.
    """
    if not message.text:
        return False
    
    text_lower = message.text.strip().lower()
    
    if text_lower == "/start":
        await cmd_start(message, state)
        return True
    if text_lower in {"/profile", "обновить профиль"}:
        await update_profile(message, state)
        return True
    if text_lower in {"/newplan", "новый план"}:
        await start_new_plan(message, state)
        return True
    if text_lower in {"/help", "подсказки"}:
        await show_help(message)
        return True
    
    return False


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
    
    # Проверяем, используются ли этапы
    use_stages = data.get("use_stages", False)
    plan_stages = data.get("plan_stages", [])
    current_stage_index = data.get("current_stage_index", None)
    
    if use_stages and plan_stages and current_stage_index is not None:
        # Работаем с этапами - добавляем услуги к текущему этапу
        if current_stage_index < len(plan_stages):
            current_stage = plan_stages[current_stage_index]
            # Получаем существующие items текущего этапа
            existing_stage_items = current_stage.get("items", [])
            # Объединяем с новыми items
            stage_combined = combine_plans(
                {"items": existing_stage_items, "total": 0},
                new_plan,
                current_stage.get("codes", []) + codes
            )
            # Обновляем текущий этап
            current_stage["items"] = stage_combined.get("items", [])
            current_stage["codes"] = list(set(current_stage.get("codes", []) + codes))
            plan_stages[current_stage_index] = current_stage
            
            # Формируем общий план из всех этапов для подсчёта итоговой суммы
            all_stage_items = []
            for stage in plan_stages:
                all_stage_items.extend(stage.get("items", []))
            
            # Объединяем все items из всех этапов в общий план
            combined_plan = combine_plans(
                {"items": all_stage_items, "total": 0},
                {"items": [], "total": 0},
                all_codes
            )
            
            # Добавляем информацию об этапах в план
            combined_plan["stages"] = plan_stages
            combined_plan["use_stages"] = True
            
            # Сохраняем обновлённые этапы в состоянии
            await state.update_data(plan_stages=plan_stages)
    else:
        # Обычный план без этапов
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

    # Формируем подробный payload для agent draft с названиями услуг
    plan_items = combined_plan.get("items", [])
    codes_with_names = [
        {
            "code": item.get("code", ""),
            "name": item.get("display_name", ""),
            "section": item.get("section", ""),
            "count": item.get("count", 1),
            "price": item.get("base_price", 0),
        }
        for item in plan_items
    ]
    
    agent_payload = {
        "doctor": data.get("doctor_full_display") or data.get("doctor") or "",
        "patient": data.get("patient", ""),
        "card": data.get("card", ""),
        "intake": data.get("intake", ""),
        "codes": all_codes,
        "services": codes_with_names,  # Добавляем подробную информацию об услугах
        "total": combined_plan.get("total", 0),
        "items_count": len(plan_items),
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
    # Проверяем, используются ли этапы (получаем заново, т.к. могло быть обновлено)
    use_stages = data.get("use_stages", False)
    plan_stages = data.get("plan_stages", [])
    
    if use_stages and plan_stages:
        # Для многоэтапного плана предлагаем добавить следующий этап или завершить
        await message.answer(
            "📋 Добавить следующий этап лечения или завершить план?\n"
            "Напиши 'следующий этап' для создания нового этапа, или 'завершить' для финализации плана.",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await message.answer(
            "Продолжить добавление услуг или завершить план? Напиши 'продолжить' или 'завершить'.",
            reply_markup=MAIN_KEYBOARD,
        )
    await state.set_state(SessionState.plan_confirm)


async def finalize_current_plan(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan = data.get("plan")
    use_stages = data.get("use_stages", False)
    plan_stages = data.get("plan_stages", [])
    
    # Для многоэтапных планов проверяем, что есть хотя бы один этап с услугами
    if use_stages and plan_stages:
        has_items = any(stage.get("items") for stage in plan_stages)
        if not has_items:
            await message.answer(
                "План пустой. Добавь услуги или опиши их словами, чтобы я сформировал финальную версию.",
                reply_markup=MAIN_KEYBOARD,
            )
            await state.set_state(SessionState.plan_codes)
            return
    elif not plan or not plan.get("items"):
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

    # Сохраняем шаблон плана для обучения модели на предпочтениях врача
    doctor_id = data.get("doctor_id")
    ideological_plan = data.get("intake", "").strip()
    all_codes = data.get("codes", [])
    template_adapted = data.get("template_adapted", False)
    selected_template_id = data.get("selected_template_id")
    
    if doctor_id and ideological_plan and all_codes:
        try:
            # Извлекаем последовательность кодов из плана (в порядке добавления)
            plan_items = plan.get("items", [])
            codes_sequence = [item.get("code") for item in plan_items if item.get("code")]
            
            if codes_sequence:
                with get_db() as db:
                    # Проверяем, существует ли уже такой шаблон
                    existing_template = db.query(PlanTemplate).filter_by(
                        doctor_id=doctor_id,
                        ideological_plan=ideological_plan,
                    ).first()
                    
                    if existing_template:
                        # Если шаблон был адаптирован из другого шаблона - создаём новый шаблон с новой формулировкой
                        if template_adapted and selected_template_id and selected_template_id != existing_template.id:
                            # Создаём новый шаблон с новой формулировкой для обучения
                            new_template = PlanTemplate(
                                doctor_id=doctor_id,
                                ideological_plan=ideological_plan,
                                codes_sequence=codes_sequence,
                                plan_metadata=extract_plan_metadata(ideological_plan, plan_items),
                                source_plan_id=plan_id,
                                usage_count=1,
                            )
                            db.add(new_template)
                            db.flush()
                            new_template_id = new_template.id
                            db.commit()
                            logging.info(
                                f"Created adapted template {new_template_id} from template {selected_template_id} for doctor {doctor_id}"
                            )
                            
                            # Сохраняем эмбеддинг в Qdrant
                            try:
                                loop = asyncio.get_event_loop()
                                loop.run_in_executor(
                                    None,
                                    save_template_embedding,
                                    new_template_id,
                                    ideological_plan,
                                    doctor_id,
                                    codes_sequence,
                                    extract_plan_metadata(ideological_plan, plan_items),
                                )
                            except Exception as qdrant_exc:
                                logging.warning(f"Failed to save adapted template {new_template_id} to Qdrant: {qdrant_exc}")
                        else:
                            # Увеличиваем счётчик использования существующего шаблона
                            existing_template.usage_count += 1
                            existing_template.updated_at = datetime.utcnow()
                            template_id = existing_template.id
                            db.commit()
                            logging.info(f"Updated existing template {template_id} for doctor {doctor_id}")
                    else:
                        # Создаём новый шаблон
                        # Извлекаем метаданные из идеологического плана
                        plan_metadata = extract_plan_metadata(ideological_plan, plan_items)
                        
                        new_template = PlanTemplate(
                            doctor_id=doctor_id,
                            ideological_plan=ideological_plan,
                            codes_sequence=codes_sequence,
                            plan_metadata=plan_metadata,
                            source_plan_id=plan_id,
                            usage_count=1,
                        )
                        db.add(new_template)
                        db.flush()
                        template_id = new_template.id
                        db.commit()
                        logging.info(f"Created new template {template_id} for doctor {doctor_id}")
                        
                        # Сохраняем эмбеддинг в Qdrant асинхронно (в фоне)
                        try:
                            loop = asyncio.get_event_loop()
                            loop.run_in_executor(
                                None,
                                save_template_embedding,
                                template_id,
                            ideological_plan,
                            doctor_id,
                            codes_sequence,
                            extract_plan_metadata(ideological_plan, plan_items),
                            )
                        except Exception as qdrant_exc:
                            logging.warning(f"Failed to save template {template_id} to Qdrant: {qdrant_exc}")
        except Exception as template_exc:
            logging.exception(f"Failed to save plan template: {template_exc}")

    base_state = {key: data[key] for key in ("doctor", "doctor_id", "doctor_full_display") if data.get(key)}
    await state.set_data(base_state)
    await message.answer(
        "План сохранён. Укажи следующего пациента или нажми 'Новый план'.",
        reply_markup=MAIN_KEYBOARD,
    )
    await state.set_state(SessionState.patient)


def extract_plan_metadata(ideological_plan: str, plan_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Извлекает метаданные из идеологического плана и элементов плана.
    
    Метаданные включают:
    - Бренды (Straumann, Astra, Nobel и т.д.)
    - Локализацию (зуб 2.5, зуб 3.5 и т.д.)
    - Особенности (одномоментная, отсроченная и т.д.)
    """
    metadata: Dict[str, Any] = {}
    text_lower = ideological_plan.lower()
    
    # Извлекаем бренды из текста
    brands = []
    brand_keywords = ["straumann", "astra", "nobel", "osstem", "bicon", "implantium", "e.max", "металлокерамика"]
    for brand in brand_keywords:
        if brand in text_lower:
            brands.append(brand.title())
    if brands:
        metadata["brands"] = brands
    
    # Извлекаем локализацию (номер зуба)
    tooth_match = re.search(r"зуб\s*(\d+\.?\d*)", text_lower)
    if tooth_match:
        metadata["location"] = tooth_match.group(1)
    
    # Извлекаем особенности
    features = []
    if "одномоментн" in text_lower:
        features.append("одномоментная")
    if "отсроченн" in text_lower:
        features.append("отсроченная")
    if "немедленн" in text_lower:
        features.append("немедленная нагрузка")
    if features:
        metadata["features"] = features
    
    return metadata


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
    """Форматирует план лечения, учитывая этапы если они есть."""
    use_stages = plan.get("use_stages", False)
    stages = plan.get("stages", [])
    
    if use_stages and stages:
        # Форматируем многоэтапный план
        lines = []
        total_all = 0.0
        
        for stage_idx, stage in enumerate(stages, start=1):
            stage_name = stage.get("name", f"Этап {stage_idx}")
            stage_items = stage.get("items", [])
            stage_total = 0.0
            
            if stage_items:
                lines.append(f"\n📋 {stage_name}:")
                for item in stage_items:
                    code = item.get("code", "")
                    name = item.get("display_name", "")
                    count = item.get("count", 1)
                    item_sum = item.get("sum", 0) if "sum" in item else (item.get("base_price", 0) * count)
                    stage_total += item_sum
                    lines.append(f"  • {code}: {name} × {count} → {item_sum:.2f} ₽")
                lines.append(f"  Итого по этапу: {stage_total:.2f} ₽")
                total_all += stage_total
        
        # Если есть общие items (для обратной совместимости), добавляем их
        general_items = plan.get("items", [])
        if general_items and not any(stage.get("items") for stage in stages):
            # Если в этапах нет items, используем общие items
            for item in general_items:
                code = item.get("code", "")
                name = item.get("display_name", "")
                count = item.get("count", 1)
                item_sum = item.get("sum", 0)
                total_all += item_sum
                lines.append(f"• {code}: {name} × {count} → {item_sum:.2f} ₽")
        
        body = "\n".join(lines) if lines else "(пусто)"
        return f"{body}\n\n💰 Общая сумма: {total_all:.2f} ₽"
    else:
        # Обычный формат без этапов
        lines = []
        for item in plan.get("items", []):
            code = item.get("code", "")
            name = item.get("display_name", "")
            count = item.get("count", 1)
            item_sum = item.get("sum", 0)
            lines.append(f"• {code}: {name} × {count} → {item_sum:.2f} ₽")
        total = plan.get("total", 0)
        body = "\n".join(lines) if lines else "(пусто)"
        return f"{body}\n\nИтого: {total:.2f} ₽"

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
    # Проверяем команды - они имеют приоритет над FSM состояниями
    if await check_commands_in_state(message, state):
        return
    
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
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    specialization = message.text.strip()
    await state.update_data(specialization=specialization)
    await message.answer("Ученая степень (например: к.м.н. или напиши 'нет').", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_degree)


@dp.message(SessionState.doctor_degree)
async def handle_degree(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    degree = message.text.strip()
    await state.update_data(degree=degree)
    await message.answer("Квалификационная категория (высшая/первая/вторая/нет).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_category)


@dp.message(SessionState.doctor_category)
async def handle_category(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    category = message.text.strip()
    await state.update_data(category=category)
    await message.answer("Стаж (в годах).", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.doctor_experience)


@dp.message(SessionState.doctor_experience)
async def handle_experience(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
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
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    patient_name = message.text.strip()
    await state.update_data(patient=patient_name)
    await message.answer("📄 Номер амбулаторной карты?", reply_markup=MAIN_KEYBOARD)
    await state.set_state(SessionState.card_number)

@dp.message(SessionState.card_number)
async def handle_card(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
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

async def process_voice_message(message: Message, state: FSMContext) -> Optional[str]:
    """
    Универсальная функция для обработки голосовых сообщений.
    Распознаёт голос и возвращает текст, или None в случае ошибки.
    """
    await message.answer("⌛ Распознаю аудио...")
    try:
        file_path = await download_voice(bot, message)
        text = await transcribe_voice(file_path)
        
        if not text or not text.strip():
            await message.answer(
                "Не удалось распознать текст из голосового сообщения. Попробуй записать заново.",
                reply_markup=MAIN_KEYBOARD,
            )
            return None
        
        # Показываем распознанный текст пользователю
        await message.answer(f"🎙 Распознал: {text}", reply_markup=MAIN_KEYBOARD)
        return text.strip()
    except Exception as voice_exc:
        logging.exception(f"Voice transcription failed: {voice_exc}")
        await message.answer(
            "❌ Не удалось распознать голосовое сообщение. "
            "Попробуй отправить текст или записать голосовое сообщение заново.",
            reply_markup=MAIN_KEYBOARD,
        )
        return None


@dp.message(SessionState.intake, F.voice)
async def handle_voice(message: Message, state: FSMContext):
    text = await process_voice_message(message, state)
    if not text:
        return
    
    ideological_plan = text
    await state.update_data(intake=ideological_plan)
    
    data = await state.get_data()
    doctor_id = data.get("doctor_id")
    
    # Ищем похожие шаблоны планов в истории врача (с таймаутом и обработкой ошибок)
    similar_templates = []
    if doctor_id and ideological_plan:
        try:
            await message.answer("🔍 Ищу похожие планы в истории...")
            # Добавляем явный таймаут для поиска (увеличиваем до 5 секунд и снижаем порог до 0.5)
            search_task = search_similar_plan_templates(
                ideological_plan=ideological_plan,
                doctor_id=doctor_id,
                top_k=5,  # Увеличиваем с 3 до 5 для большего выбора
                score_threshold=0.35,  # Снижаем порог с 0.5 до 0.35 для более широкого поиска
            )
            similar_templates = await asyncio.wait_for(search_task, timeout=5.0)  # Увеличиваем таймаут до 5 секунд
            
            # Если поиск вернул пустой список, просто продолжаем без предложения шаблонов
            if not similar_templates:
                logging.debug(f"No similar templates found for doctor {doctor_id}")
        except asyncio.TimeoutError:
            logging.warning(f"Template search timed out for doctor {doctor_id} after 5 seconds")
            # Не показываем ошибку пользователю, просто продолжаем без предложения шаблонов
            similar_templates = []
        except Exception as exc:
            logging.exception(f"Template search failed for doctor {doctor_id}: {exc}")
            # Не показываем ошибку пользователю, просто продолжаем без предложения шаблонов
            similar_templates = []
    
    # Если найдены похожие шаблоны - предлагаем врачу выбрать
    if similar_templates:
        await state.update_data(similar_templates=similar_templates)
        
        options = []
        for idx, template in enumerate(similar_templates, start=1):
            plan_text = template["ideological_plan"]
            codes_count = len(template.get("codes_sequence", []))
            score = template.get("score", 0.0)
            score_percent = int(score * 100)
            
            # Обрезаем длинный текст
            plan_preview = plan_text[:80] + "..." if len(plan_text) > 80 else plan_text
            
            options.append(
                f"{idx}. {plan_preview}\n"
                f"   Кодов: {codes_count}, схожесть: {score_percent}%"
            )
        
        options_text = "\n\n".join(options)
        await message.answer(
            f"🎙 Распознал: {text}\n\n"
            f"📋 Нашёл похожие планы в истории:\n\n{options_text}\n\n"
            "Выбери номер плана для использования или напиши 'новый' для создания нового плана.",
            reply_markup=MAIN_KEYBOARD,
        )
        await maybe_smalltalk(message, "intake_ack", reply_markup=None, intake=text)
        await state.set_state(SessionState.template_selection)
        return
    
    # Если похожих шаблонов нет - спрашиваем про этапы лечения
    await message.answer(
        f"🎙 Распознал: {text}\n\n"
        "📋 План лечения будет многоэтапным? "
        "Например: первый этап - пародонтология и санация, второй - временное протезирование.\n\n"
        "Напиши 'да' если нужны этапы, или 'нет' для простого плана.",
        reply_markup=MAIN_KEYBOARD,
    )
    await maybe_smalltalk(message, "intake_ack", reply_markup=None, intake=text)
    await state.set_state(SessionState.plan_stage_selection)


@dp.message(SessionState.intake)
async def handle_intake(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    ideological_plan = message.text.strip()
    await state.update_data(intake=ideological_plan)
    
    data = await state.get_data()
    doctor_id = data.get("doctor_id")
    
    # Ищем похожие шаблоны планов в истории врача (с таймаутом и обработкой ошибок)
    similar_templates = []
    if doctor_id and ideological_plan:
        try:
            await message.answer("🔍 Ищу похожие планы в истории...")
            # Добавляем явный таймаут для поиска (увеличиваем до 5 секунд и снижаем порог до 0.5)
            search_task = search_similar_plan_templates(
                ideological_plan=ideological_plan,
                doctor_id=doctor_id,
                top_k=5,  # Увеличиваем с 3 до 5 для большего выбора
                score_threshold=0.35,  # Снижаем порог с 0.5 до 0.35 для более широкого поиска
            )
            similar_templates = await asyncio.wait_for(search_task, timeout=5.0)  # Увеличиваем таймаут до 5 секунд
            
            # Если поиск вернул пустой список, просто продолжаем без предложения шаблонов
            if not similar_templates:
                logging.debug(f"No similar templates found for doctor {doctor_id}")
        except asyncio.TimeoutError:
            logging.warning(f"Template search timed out for doctor {doctor_id} after 5 seconds")
            # Не показываем ошибку пользователю, просто продолжаем без предложения шаблонов
            similar_templates = []
        except Exception as exc:
            logging.exception(f"Template search failed for doctor {doctor_id}: {exc}")
            # Не показываем ошибку пользователю, просто продолжаем без предложения шаблонов
            similar_templates = []
    
    # Если найдены похожие шаблоны - предлагаем врачу выбрать
    if similar_templates:
        await state.update_data(similar_templates=similar_templates)
        
        options = []
        for idx, template in enumerate(similar_templates, start=1):
            plan_text = template["ideological_plan"]
            codes_count = len(template.get("codes_sequence", []))
            score = template.get("score", 0.0)
            score_percent = int(score * 100)
            
            # Обрезаем длинный текст
            plan_preview = plan_text[:80] + "..." if len(plan_text) > 80 else plan_text
            
            options.append(
                f"{idx}. {plan_preview}\n"
                f"   Кодов: {codes_count}, схожесть: {score_percent}%"
            )
        
        options_text = "\n\n".join(options)
        await message.answer(
            f"📋 Нашёл похожие планы в истории:\n\n{options_text}\n\n"
            "Выбери номер плана для использования или напиши 'новый' для создания нового плана.",
            reply_markup=MAIN_KEYBOARD,
        )
        await maybe_smalltalk(message, "intake_ack", reply_markup=None, intake=ideological_plan)
        await state.set_state(SessionState.template_selection)
        return
    
    # Если похожих шаблонов нет - спрашиваем про этапы лечения
    await message.answer(
        "📋 План лечения будет многоэтапным? "
        "Например: первый этап - пародонтология и санация, второй - временное протезирование.\n\n"
        "Напиши 'да' если нужны этапы, или 'нет' для простого плана.",
        reply_markup=MAIN_KEYBOARD,
    )
    await maybe_smalltalk(message, "intake_ack", reply_markup=None, intake=ideological_plan)
    await state.set_state(SessionState.plan_stage_selection)


# Обработчик голосовых сообщений для plan_stage_selection
@dp.message(SessionState.plan_stage_selection, F.voice)
async def handle_voice_plan_stage_selection(message: Message, state: FSMContext):
    text = await process_voice_message(message, state)
    if not text:
        return
    message.text = text
    await handle_plan_stage_selection(message, state)


@dp.message(SessionState.plan_stage_selection)
async def handle_plan_stage_selection(message: Message, state: FSMContext):
    """Обработка выбора многоэтапного плана или простого плана."""
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    raw = (message.text or "").strip().lower()
    
    # Если пользователь хочет многоэтапный план
    if raw in {"да", "yes", "многоэтапный", "этапы", "этапный"}:
        # Создаём структуру для этапов в состоянии
        stages = []
        current_stage = {
            "name": "Первый этап",
            "items": [],
            "codes": [],
        }
        stages.append(current_stage)
        
        await state.update_data(
            plan_stages=stages,
            current_stage_index=0,
            use_stages=True,
        )
        
        await message.answer(
            "📋 Назови первый этап лечения (например: 'Пародонтология и санация', 'Разборка старых конструкций', 'Хирургия').\n\n"
            "Или напиши 'пропустить' чтобы начать с добавления услуг.",
            reply_markup=MAIN_KEYBOARD,
        )
        # Пока оставляем переход к plan_codes, но можно добавить отдельное состояние для названия этапа
        # Для упрощения, если пользователь сразу пишет название этапа - используем его
        await state.set_state(SessionState.plan_codes)
        return
    
    # Если пользователь не хочет этапы или сказал "нет"
    if raw in {"нет", "no", "простой", "без этапов", "обычный"}:
        await state.update_data(
            plan_stages=[],
            current_stage_index=None,
            use_stages=False,
        )
        await message.answer(
            "Ок, создаю простой план. Отправь коды услуг или опиши словами (например: 'имплантат Straumann').",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.set_state(SessionState.plan_codes)
        return
    
    # Если ответ не распознан - возможно, пользователь сразу указал название этапа
    # Или пытаемся понять через LLM
    if len(raw) > 5:  # Если текст достаточно длинный, возможно это название этапа
        # Создаём этапы с указанным названием
        stages = []
        current_stage = {
            "name": message.text.strip(),  # Используем оригинальный текст (не lower)
            "items": [],
            "codes": [],
        }
        stages.append(current_stage)
        
        await state.update_data(
            plan_stages=stages,
            current_stage_index=0,
            use_stages=True,
        )
        
        await message.answer(
            f"✅ Создаю первый этап: {current_stage['name']}\n\n"
            "Теперь отправь коды услуг или опиши словами для этого этапа.",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.set_state(SessionState.plan_codes)
        return
    
    # Если не поняли - переспрашиваем
    await message.answer(
        "Не понял. Напиши 'да' для многоэтапного плана, 'нет' для простого плана, или укажи название этапа (например: 'Пародонтология и санация').",
        reply_markup=MAIN_KEYBOARD,
    )


# Обработчик голосовых сообщений для template_selection
@dp.message(SessionState.template_selection, F.voice)
async def handle_voice_template_selection(message: Message, state: FSMContext):
    text = await process_voice_message(message, state)
    if not text:
        return
    message.text = text
    await handle_template_selection(message, state)


# Обработчик голосовых сообщений для plan_confirm
@dp.message(SessionState.plan_confirm, F.voice)
async def handle_voice_plan_confirm(message: Message, state: FSMContext):
    text = await process_voice_message(message, state)
    if not text:
        return
    message.text = text
    await handle_plan_confirm(message, state)


@dp.message(SessionState.template_selection)
async def handle_template_selection(message: Message, state: FSMContext):
    """Обработка выбора шаблона плана из истории."""
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    data = await state.get_data()
    similar_templates: List[Dict[str, Any]] = data.get("similar_templates", [])
    
    if not similar_templates:
        await message.answer("Шаблоны не найдены. Создаю новый план.", reply_markup=MAIN_KEYBOARD)
        await state.set_state(SessionState.plan_codes)
        return
    
    user_input = message.text.strip().lower()
    
    # Если пользователь хочет создать новый план
    if user_input in {"новый", "new", "создать", "create"}:
        await state.update_data(similar_templates=None)
        await message.answer(
            "Создаю новый план. Отправь коды услуг или опиши словами (например: 'имплантат Straumann').",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.set_state(SessionState.plan_codes)
        return
    
    # Пытаемся распознать номер шаблона
    indexes = parse_choice_indexes(user_input)
    if not indexes or len(indexes) != 1:
        await message.answer(
            "Не понял выбор. Напиши номер плана (1, 2 или 3) или 'новый' для создания нового плана.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    
    selected_idx = indexes[0]
    if selected_idx < 0 or selected_idx >= len(similar_templates):
        await message.answer(
            f"Неверный номер. Выбери от 1 до {len(similar_templates)} или напиши 'новый'.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    
    # Используем выбранный шаблон
    selected_template = similar_templates[selected_idx]
    original_codes = selected_template.get("codes_sequence", [])
    original_plan = selected_template.get("ideological_plan", "")
    
    if not original_codes:
        await message.answer(
            "В выбранном шаблоне нет кодов. Создаю новый план.",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.update_data(similar_templates=None)
        await state.set_state(SessionState.plan_codes)
        return
    
    # Получаем новый контекст (текущий идеологический план)
    new_context = data.get("intake", "").strip()
    
    # Адаптируем план под новый контекст через LLM
    adapted_codes = original_codes
    adapted_plan = original_plan
    changes_text = "Использую план без изменений"
    
    if LLM_CLIENT and new_context and new_context != original_plan:
        await message.answer("🔄 Адаптирую план под новый контекст...")
        try:
            adaptation = await with_typing_action(
                message.chat.id,
                asyncio.wait_for(
                    LLM_CLIENT.adapt_plan(
                        original_plan=original_plan,
                        original_codes=original_codes,
                        new_context=new_context,
                    ),
                    timeout=10.0,
                ),
            )
            
            if adaptation:
                adapted_plan = adaptation.get("adapted_plan", new_context)
                adapted_codes = adaptation.get("adapted_codes", original_codes)
                changes_text = adaptation.get("changes", "Параметры обновлены")
                
                # Обновляем идеологический план в состоянии
                await state.update_data(intake=adapted_plan)
        except Exception as adapt_exc:
            logging.warning(f"Plan adaptation failed: {adapt_exc}")
            # Используем исходные коды, если адаптация не удалась
    
    # Сохраняем информацию о том, что использовали шаблон
    await state.update_data(
        selected_template_id=selected_template.get("template_id"),
        template_source="history",
        similar_templates=None,
        template_adapted=True,
    )
    
    # Используем коды из шаблона для создания плана
    await message.answer(
        f"✅ Использую план из истории:\n{adapted_plan}\n\n"
        f"{changes_text}\n"
        f"Применяю {len(adapted_codes)} кодов...",
        reply_markup=MAIN_KEYBOARD,
    )
    
    # Обрабатываем коды из шаблона (адаптированные или оригинальные)
    await process_codes(message, state, adapted_codes)


# Обработчик голосовых сообщений для plan_codes
@dp.message(SessionState.plan_codes, F.voice)
async def handle_voice_plan_codes(message: Message, state: FSMContext):
    text = await process_voice_message(message, state)
    if not text:
        return
    # Создаём фиктивное текстовое сообщение
    message.text = text
    await handle_plan_codes(message, state)


@dp.message(SessionState.plan_codes)
async def handle_plan_codes(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    raw = (message.text or "").strip()
    if not raw:
        return
    
    # Проверяем, используется ли многоэтапный план и является ли текст названием этапа
    data = await state.get_data()
    use_stages = data.get("use_stages", False)
    plan_stages = data.get("plan_stages", [])
    current_stage_index = data.get("current_stage_index", None)
    
    # Если используется этапы и текст похож на название этапа (длинный, без кодов, содержит медицинские термины)
    if use_stages and current_stage_index is not None and plan_stages:
        stage_name_keywords = ["этап", "пародонтолог", "санация", "протезирование", "хирургия", "ортодонтия", "терапия", "лечение", "разборка", "конструкция"]
        contains_stage_keywords = any(kw in raw.lower() for kw in stage_name_keywords)
        contains_codes_pattern = bool(re.search(r'\b\d{6}\b', raw))
        
        # Если текст похож на название этапа (нет кодов, есть ключевые слова этапов, длина > 10)
        if not contains_codes_pattern and contains_stage_keywords and len(raw) > 10:
            # Обновляем название текущего этапа
            if current_stage_index < len(plan_stages):
                plan_stages[current_stage_index]["name"] = raw
                await state.update_data(plan_stages=plan_stages)
                await message.answer(
                    f"✅ Название этапа обновлено: {raw}\n\n"
                    "Теперь отправь коды услуг или опиши словами для этого этапа.",
                    reply_markup=MAIN_KEYBOARD,
                )
                return
    
    # Если сообщение похоже на вопрос или не похоже на код/описание услуги - используем LLM
    if LLM_CLIENT and (raw.endswith("?") or len(raw.split()) > 5 or raw.lower() in {"найди в базе", "найти", "поиск"}):
        try:
            await message.answer("🤔 Пытаюсь понять, что ты имеешь в виду...")
            current_state_obj = await state.get_state()
            current_state = current_state_obj.state if current_state_obj else None
            state_data = await state.get_data()
            intent_result = await asyncio.wait_for(
                LLM_CLIENT.understand_intent(
                    user_message=raw,
                    current_state=current_state,
                    state_data=state_data,
                    conversation_history=[],
                ),
                timeout=5.0,
            )
            if intent_result:
                action = intent_result.get("action", "")
                explanation = intent_result.get("explanation", "")
                if action == "answer_question" and explanation:
                    await message.answer(explanation, reply_markup=MAIN_KEYBOARD)
                    return
                elif action == "redirect_to_codes" and explanation:
                    await message.answer(f"{explanation}\n\nОтправь коды услуг или опиши словами.", reply_markup=MAIN_KEYBOARD)
                    return
        except Exception as llm_exc:
            logging.debug(f"LLM fallback in plan_codes failed: {llm_exc}")

    if not re.search(r"[\wа-яА-ЯёЁ]", raw) or len(raw) < 2:
        # Если LLM доступен, пытаемся понять намерение
        if LLM_CLIENT:
            try:
                current_state_obj = await state.get_state()
                current_state = current_state_obj.state if current_state_obj else None
                state_data = await state.get_data()
                intent_result = await asyncio.wait_for(
                    LLM_CLIENT.understand_intent(
                        user_message=raw,
                        current_state=current_state,
                        state_data=state_data,
                        conversation_history=[],
                    ),
                    timeout=5.0,
                )
                if intent_result and intent_result.get("action") == "answer_question":
                    explanation = intent_result.get("explanation", "")
                    if explanation:
                        await message.answer(explanation, reply_markup=MAIN_KEYBOARD)
                        return
            except Exception as llm_exc:
                logging.debug(f"LLM fallback failed: {llm_exc}")
        
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
            # Если семантический поиск недоступен, пробуем LLM для понимания намерения
            if LLM_CLIENT:
                try:
                    await message.answer("🤔 Пытаюсь понять, что ты имеешь в виду...")
                    current_state_obj = await state.get_state()
                    current_state = current_state_obj.state if current_state_obj else None
                    state_data = await state.get_data()
                    intent_result = await asyncio.wait_for(
                        LLM_CLIENT.understand_intent(
                            user_message=raw,
                            current_state=current_state,
                            state_data=state_data,
                            conversation_history=[],
                        ),
                        timeout=5.0,
                    )
                    if intent_result and intent_result.get("action") == "answer_question":
                        explanation = intent_result.get("explanation", "")
                        if explanation:
                            await message.answer(explanation, reply_markup=MAIN_KEYBOARD)
                            return
                except Exception as llm_exc:
                    logging.debug(f"LLM fallback failed: {llm_exc}")
            
            await message.answer(
                "⚠️ Не получилось подобрать услуги по описанию. Попробуй сформулировать иначе или введи коды вручную.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        if not picks:
            # Если результаты найдены, но пустые - возможно, запрос слишком общий
            # Пробуем LLM для понимания намерения
            if LLM_CLIENT and len(raw) > 3:  # Только для достаточно длинных запросов
                try:
                    await message.answer("🤔 Уточняю запрос...")
                    current_state_obj = await state.get_state()
                    current_state = current_state_obj.state if current_state_obj else None
                    state_data = await state.get_data()
                    intent_result = await asyncio.wait_for(
                        LLM_CLIENT.understand_intent(
                            user_message=raw,
                            current_state=current_state,
                            state_data=state_data,
                            conversation_history=[],
                        ),
                        timeout=5.0,
                    )
                    if intent_result:
                        explanation = intent_result.get("explanation", "")
                        if explanation:
                            await message.answer(
                                f"{explanation}\n\nПопробуй уточнить формулировку или указать код.",
                                reply_markup=MAIN_KEYBOARD,
                            )
                            return
                except Exception as llm_exc:
                    logging.debug(f"LLM clarification failed: {llm_exc}")
            
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


# Обработчик голосовых сообщений для plan_disambiguation
@dp.message(SessionState.plan_disambiguation, F.voice)
async def handle_voice_plan_disambiguation(message: Message, state: FSMContext):
    text = await process_voice_message(message, state)
    if not text:
        return
    message.text = text
    await handle_plan_disambiguation(message, state)


@dp.message(SessionState.plan_disambiguation)
async def handle_plan_disambiguation(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    raw = (message.text or "").strip()
    raw_lower = raw.lower()
    
    # Проверяем естественные выходы
    if raw_lower in {"отмена", "cancel", "нет", "no", "назад", "back"}:
        await state.update_data(candidate_codes=None, raw_text=None)
        await message.answer("Окей, выбери коды заново или опиши услуги ещё раз.", reply_markup=MAIN_KEYBOARD)
        await state.set_state(SessionState.plan_codes)
        return
    
    if raw_lower in {"завершить", "finish", "готово", "done", "оценить", "оценить план"}:
        # Переходим к финализации плана
        await state.update_data(candidate_codes=None, raw_text=None)
        data = await state.get_data()
        if data.get("plan_id"):
            await state.set_state(SessionState.plan_confirm)
            await message.answer("Переходим к финализации плана.", reply_markup=MAIN_KEYBOARD)
            # Показываем текущий план и предлагаем завершить
            plan = data.get("plan")
            if plan:
                summary = format_plan(plan)
                await message.answer(summary, reply_markup=MAIN_KEYBOARD)
            await message.answer(
                "Продолжить добавление услуг или завершить план? Напиши 'продолжить' или 'завершить'.",
                reply_markup=MAIN_KEYBOARD,
            )
        else:
            await message.answer("Сначала добавь услуги в план.", reply_markup=MAIN_KEYBOARD)
            await state.set_state(SessionState.plan_codes)
        return
    
    data = await state.get_data()
    candidates: List[Dict[str, Any]] = data.get("candidate_codes", [])
    
    # Если пользователь ввёл коды или описание услуг - обрабатываем как новый запрос
    contains_codes = bool(re.search(r'\b\d{6}\b', raw))  # Проверяем наличие 6-значных кодов
    medical_keywords = ["удаление", "имплант", "анестезия", "формирователь", "straumann", "astra", "коронка", "швы", "коллаген", "синус", "лифтинг"]
    contains_medical_terms = any(kw in raw_lower for kw in medical_keywords)
    
    if contains_codes or (contains_medical_terms and len(raw.split()) >= 2):
        # Это новый запрос на услуги, обрабатываем как коды
        await state.update_data(candidate_codes=None, raw_text=None)
        await state.set_state(SessionState.plan_codes)
        # Рекурсивно вызываем обработчик кодов
        await handle_plan_codes(message, state)
        return
    
    if not candidates:
        await message.answer("Кандидаты не найдены. Начни ввод кодов заново.", reply_markup=MAIN_KEYBOARD)
        await state.set_state(SessionState.plan_codes)
        return

    # Пытаемся распознать номера из списка
    indexes = parse_choice_indexes(raw)
    if not indexes:
        await message.answer(
            "Не понял выбор. Укажи номера через запятую (например: 1,2), "
            "или напиши 'отмена' для отмены, 'завершить' для финализации плана.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    selected: List[str] = []
    for idx in indexes:
        if idx < len(candidates):
            selected.append(candidates[idx]["code"])

    if not selected:
        await message.answer("Ни один номер не распознан. Повтори выбор.", reply_markup=MAIN_KEYBOARD)
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
    data = await state.get_data()
    use_stages = data.get("use_stages", False)
    
    if use_stages:
        await message.answer(
            "Ок, добавим ещё услуги в текущий этап. Напиши коды или опиши словами.",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await message.answer(
            "Ок, добавим ещё услуги. Напиши коды или опиши словами следующую часть плана.",
            reply_markup=MAIN_KEYBOARD,
        )
    await state.set_state(SessionState.plan_codes)

@dp.message(SessionState.plan_confirm)
async def handle_plan_confirm(message: Message, state: FSMContext):
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
    raw = (message.text or "").strip().lower()
    data = await state.get_data()
    use_stages = data.get("use_stages", False)
    plan_stages = data.get("plan_stages", [])

    if raw in CONFIRM_WORDS:
        await finalize_current_plan(message, state)
        return
    
    # Обработка добавления следующего этапа для многоэтапного плана
    if use_stages and raw in {"следующий этап", "новый этап", "добавить этап", "этап", "следующий"}:
        # Создаём новый этап
        new_stage_index = len(plan_stages)
        new_stage = {
            "name": f"Этап {new_stage_index + 1}",
            "items": [],
            "codes": [],
        }
        plan_stages.append(new_stage)
        await state.update_data(
            plan_stages=plan_stages,
            current_stage_index=new_stage_index,
        )
        await message.answer(
            f"📋 Создаю этап {new_stage_index + 1}.\n"
            "Укажи название этапа (например: 'Пародонтология и санация', 'Временное протезирование') или начни добавлять услуги.",
            reply_markup=MAIN_KEYBOARD,
        )
        await state.set_state(SessionState.plan_codes)
        return

    if raw in DECLINE_WORDS:
        # Сохраняем текущий контекст (пациент, карта, план) и переходим к редактированию
        data = await state.get_data()
        await message.answer(
            "🔁 Принято. Напиши коды услуг для добавления/изменения или опиши словами (например: 'удаление сложное, Straumann, формирователь Straumann').",
            reply_markup=MAIN_KEYBOARD,
        )
        # Переходим к редактированию кодов, сохраняя контекст
        await state.set_state(SessionState.plan_codes)
        return
    
    # Если сообщение похоже на описание услуг (содержит медицинские термины или коды) - обрабатываем как коды
    medical_keywords = ["удаление", "имплант", "анестезия", "формирователь", "straumann", "astra", "коронка", "швы", "коллаген"]
    contains_codes = bool(re.search(r'\b\d{6}\b', raw))  # Проверяем наличие 6-значных кодов
    contains_medical_terms = any(kw in raw.lower() for kw in medical_keywords)
    
    if contains_codes or (contains_medical_terms and len(raw.split()) >= 2):
        # Это описание услуг, обрабатываем как коды
        await state.set_state(SessionState.plan_codes)
        # Рекурсивно вызываем обработчик кодов
        await handle_plan_codes(message, state)
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
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
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
    # Проверяем команды
    if await check_commands_in_state(message, state):
        return
    
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
    # Полностью очищаем состояние FSM
    await state.clear()
    
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


# Обработчик свободных текстовых сообщений с LLM для полноценного диалога
# ВАЖНО: Этот обработчик должен быть последним, чтобы не перехватывать сообщения из состояний FSM
# В aiogram обработчики выполняются в порядке регистрации, поэтому более специфичные (@dp.message(SessionState.X)) обрабатываются первыми
@dp.message(F.text)
async def handle_free_text(message: Message, state: FSMContext):
    """
    Обработчик для свободных текстовых сообщений, которые не попали в другие обработчики.
    Использует LLM для понимания намерения и ведения диалога.
    
    ВАЖНО: Этот обработчик срабатывает ТОЛЬКО если сообщение не было обработано обработчиками состояний FSM.
    """
    # Пропускаем команды - они обрабатываются отдельными обработчиками
    text_lower = (message.text or "").strip().lower()
    if text_lower.startswith("/") or text_lower in {"назад", "подсказки", "оценить план"}:
        return
    
    # Проверяем команды через функцию
    if await check_commands_in_state(message, state):
        return
    
    # Проверяем, что мы не в состоянии FSM (если в состоянии - сообщение уже обработано специфичным обработчиком)
    current_state_obj = await state.get_state()
    if current_state_obj and current_state_obj.state:
        # Если мы в состоянии FSM, но сюда попали - значит специфичный обработчик не обработал сообщение
        # В этом случае используем LLM для понимания намерения
        pass  # Продолжаем обработку через LLM
    
    # Получаем текущее состояние и данные
    current_state_obj = await state.get_state()
    current_state = current_state_obj.state if current_state_obj else None
    state_data = await state.get_data()
    
    # Если LLM не доступен, используем простой fallback
    if not LLM_CLIENT:
        await message.answer(
            "Не понял, что ты имеешь в виду. Используй команды из меню или введи данные для текущего шага.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    
    # Показываем индикатор "печатает"
    await bot.send_chat_action(message.chat.id, "typing")
    
    try:
        # Формируем историю диалога (можно расширить для хранения полной истории)
        conversation_history = []  # Пока используем только текущее сообщение
        
        # Используем LLM для понимания намерения
        intent_result = await with_typing_action(
            message.chat.id,
            asyncio.wait_for(
                LLM_CLIENT.understand_intent(
                    user_message=message.text,
                    current_state=current_state,
                    state_data=state_data,
                    conversation_history=conversation_history,
                ),
                timeout=config.yandex_gpt_timeout + 5,
            ),
        )
        
        if not intent_result:
            await message.answer(
                "Извини, не смог понять твоё сообщение. Попробуй сформулировать иначе или используй команды из меню.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        
        intent = intent_result.get("intent", "unclear")
        action = intent_result.get("action", "ask_clarification")
        explanation = intent_result.get("explanation", "")
        extracted_data = intent_result.get("extracted_data", {})
        
        # Выполняем действие в зависимости от намерения
        if action == "redirect_to_profile":
            await update_profile(message, state)
            return
        
        if action == "redirect_to_intake":
            # Проверяем, есть ли данные о пациенте
            if not state_data.get("patient_id"):
                await message.answer(
                    "Сначала укажи пациента (ФИО/ID), а затем надиктуй план лечения.",
                    reply_markup=MAIN_KEYBOARD,
                )
                await state.set_state(SessionState.patient)
            else:
                await message.answer(
                    "🎙 Надиктуй план лечения (голос или текст).",
                    reply_markup=MAIN_KEYBOARD,
                )
                await state.set_state(SessionState.intake)
            return
        
        if action == "redirect_to_codes":
            if not state_data.get("intake"):
                await message.answer(
                    "Сначала опиши план лечения, а затем укажи коды услуг.",
                    reply_markup=MAIN_KEYBOARD,
                )
                await state.set_state(SessionState.intake)
            else:
                await message.answer(
                    "Отправь коды услуг или опиши словами (например: 'имплантат Straumann').",
                    reply_markup=MAIN_KEYBOARD,
                )
                await state.set_state(SessionState.plan_codes)
            return
        
        if action == "continue_state":
            # Продолжаем в текущем состоянии - сообщение уже обработано, просто подтверждаем
            if explanation:
                await message.answer(explanation, reply_markup=MAIN_KEYBOARD)
            else:
                await message.answer("Продолжаю...", reply_markup=MAIN_KEYBOARD)
            return
        
        if action == "answer_question":
            # Отвечаем на вопрос через LLM
            if explanation:
                await message.answer(explanation, reply_markup=MAIN_KEYBOARD)
            else:
                # Если explanation пустое, генерируем ответ через LLM
                try:
                    system_prompt = (
                        "Ты ассистент стоматолога. Отвечай на вопросы врача кратко и профессионально. "
                        "Используй только проверенную медицинскую информацию."
                    )
                    user_prompt = f"Вопрос врача: {message.text}"
                    llm_response = await asyncio.wait_for(
                        LLM_CLIENT.smalltalk(system_prompt, user_prompt),
                        timeout=config.yandex_gpt_timeout,
                    )
                    if llm_response:
                        await message.answer(llm_response, reply_markup=MAIN_KEYBOARD)
                    else:
                        await message.answer(
                            "Извини, не смог сгенерировать ответ. Попробуй сформулировать вопрос иначе.",
                            reply_markup=MAIN_KEYBOARD,
                        )
                except Exception as llm_exc:
                    logging.exception(f"LLM question answering failed: {llm_exc}")
                    await message.answer(
                        "Не смог обработать вопрос. Попробуй сформулировать иначе или используй команды из меню.",
                        reply_markup=MAIN_KEYBOARD,
                    )
            return
        
        # Fallback: просим уточнить
        if explanation:
            response_text = f"Не совсем понял. {explanation}\n\nПопробуй использовать команды из меню или сформулируй запрос иначе."
        else:
            response_text = (
                "Не понял, что ты имеешь в виду. "
                "Используй команды из меню (/start, /profile, /newplan) или введи данные для текущего шага."
            )
        
        await message.answer(response_text, reply_markup=MAIN_KEYBOARD)
    
    except asyncio.TimeoutError:
        await message.answer(
            "Извини, обработка запроса заняла слишком много времени. Попробуй сформулировать проще.",
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as exc:
        logging.exception(f"Failed to handle free text message: {exc}")
        await message.answer(
            "Произошла ошибка при обработке сообщения. Попробуй использовать команды из меню.",
            reply_markup=MAIN_KEYBOARD,
        )


async def main():
    with suppress(KeyboardInterrupt, SystemExit):
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
